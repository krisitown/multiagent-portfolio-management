"""Dated ordinary review reuse is explicit, private and parent-linked."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.support.thin_fakes import fixed_clock
from tests.thin.test_entrypoint import config, snapshot
from tests.thin.test_vault import evidence
from portfolio_management.team import run_team
from portfolio_management.types import ExpertResult, ResearchResult, TeamDependencies, TeamRequest, TeamResult


async def parent_review(tmp_path, *, record_count=1):
    class Lead:
        def __init__(self, role, ctx):
            self.ctx = ctx

        async def invoke_async(self, question):
            self.ctx.store.public_store.put("evidence", evidence("quote-old"))
            for n in range(1, record_count):
                self.ctx.store.public_store.put("evidence", evidence(f"quote-old-{n}"))
            return TeamResult(
                summary="Prior conclusion with old source dates.",
                report_markdown="# Original dated assessment\nQuote from October29; not current.\n",
            )

    result = await run_team(
        TeamRequest(
            question="PRIVATE PRIOR QUESTION",
            mode="combined",
            portfolio=snapshot(),
            portfolio_text="PRIVATE DATED PORTFOLIO",
            owner_context="Owner-stated preference, not verified capacity.",
        ),
        config=config(tmp_path),
        deps=TeamDependencies(role_factory=Lead, clock=fixed_clock),
    )
    return Path(result.artifacts["result"]).parent, result


def hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


async def test_explanation_inherits_private_context_and_dates_without_research_capabilities(
    tmp_path,
):
    parent, first = await parent_review(tmp_path / "first")
    (parent / "transcript.json").write_text("HIDDEN_TRANSCRIPT_SENTINEL")
    before = hashes(parent)
    seen = []

    class Role:
        def __init__(self, role, ctx):
            self.role, self.ctx = role, ctx
            seen.append(role)

        async def invoke_async(self, question):
            ctx = self.ctx
            assert ctx.request.owner_context == "Owner-stated preference, not verified capacity."
            assert ctx.request.portfolio_text == "PRIVATE DATED PORTFOLIO"
            assert (
                ctx.prior_review["prior_report"]
                == "# Original dated assessment\nQuote from October29; not current.\n"
            )
            assert "HIDDEN_TRANSCRIPT_SENTINEL" not in json.dumps(ctx.prior_review)
            assert any(
                row.get("source_timestamp") == "2026-10-29T20:00:00Z"
                for row in ctx.prior_review["source_dates"]
            )
            assert (
                not {
                    "explore",
                    "research_batch",
                    "request_research",
                    "market_query",
                    "public_search",
                    "fetch_sources",
                }
                & ctx.tools.keys()
            )
            if self.role == "team_lead":
                result = await ctx.tools["consult_expert"]()
                assert result["error"] is None
                return TeamResult(
                    summary="Explanation of prior dated evidence, not current advice."
                )
            return ExpertResult(assessment="The old report used October29 observations.")

    cfg = config(tmp_path / "explanation").model_copy(
        update={
            "explorer_enabled": True,
            "max_expert_consultations": 1,
            "require_fresh_opportunities": True,
        }
    )
    result = await run_team(
        TeamRequest(
            question="Why that conclusion?",
            mode="education",
            parent_review=str(parent),
            review_kind="explanation",
        ),
        config=cfg,
        deps=TeamDependencies(role_factory=lambda role, ctx: Role(role, ctx), clock=fixed_clock),
    )
    assert result.completion_reason == "complete" and seen == ["team_lead", "wheel_expert"]
    assert result.lineage["parent_run_id"] == first.run_id
    assert result.lineage["review_session_id"] == first.lineage["review_session_id"]
    assert hashes(parent) == before
    packet = json.loads(Path(result.artifacts["parent_review"]).read_text())
    assert packet["artifact_hashes"]["report.md"] == before["report.md"]


async def test_targeted_refresh_reuses_public_evidence_preserves_private_boundary_and_lineage(
    tmp_path,
):
    parent, first = await parent_review(tmp_path / "first")
    before = hashes(parent)
    seen = []

    class Role:
        def __init__(self, role, ctx):
            self.role, self.ctx = role, ctx
            seen.append(role)

        async def invoke_async(self, question):
            ctx = self.ctx
            if self.role == "team_lead":
                reports = await ctx.tools["research_batch"](
                    [
                        {
                            "job_id": "refresh",
                            "question": "Refresh public quote",
                            "evidence_refs": ["evidence:quote-old"],
                        }
                    ]
                )
                assert reports[0]["outcome"] == "success"
                await ctx.tools["consult_expert"]()
                return TeamResult(summary="New observation linked; old evidence remains dated.")
            if self.role == "analyst":
                assert ctx.prior_review is None and ctx.request.owner_context is None
                assert ctx.request.question != "PRIVATE PRIOR QUESTION"
                assert (
                    ctx.store.get("evidence:quote-old")["source_timestamp"]
                    == "2026-10-29T20:00:00Z"
                )
                ctx.store.put(
                    "evidence",
                    evidence(
                        "quote-new",
                        source_timestamp=datetime(2026, 11, 2, 15, tzinfo=UTC),
                        fetched_at=datetime(2026, 11, 2, 15, 1, tzinfo=UTC),
                    ),
                )
                return ResearchResult(
                    job_id="refresh",
                    outcome="success",
                    summary="New dated observation",
                    evidence_refs=("evidence:quote-new",),
                )
            assert ctx.store.get("evidence:quote-new")["source_timestamp"] == "2026-11-02T15:00:00Z"
            assert ctx.prior_review["prior_report"].startswith("# Original dated assessment")
            return ExpertResult(assessment="Updated only the requested decision-relevant fact.")

    result = await run_team(
        TeamRequest(
            question="Refresh this fact",
            mode="focused",
            parent_review=str(parent),
            review_kind="refresh",
            portfolio_text="NEW EXPLICIT DATED PORTFOLIO",
            owner_context="New explicit owner statement",
        ),
        config=config(tmp_path / "refresh"),
        deps=TeamDependencies(role_factory=lambda role, ctx: Role(role, ctx), clock=fixed_clock),
    )
    assert result.completion_reason == "complete" and seen == [
        "team_lead",
        "analyst",
        "wheel_expert",
    ]
    assert result.lineage["review_session_id"] == first.lineage["review_session_id"]
    assert hashes(parent) == before
    saved = json.loads(Path(result.artifacts["request"]).read_text())
    assert saved["portfolio_text"] == "NEW EXPLICIT DATED PORTFOLIO" and saved["portfolio"] is None
    assert saved["owner_context"] == "New explicit owner statement"


async def test_actual_sdk_explanation_reads_dated_parent_source_without_new_research(tmp_path):
    from collections import deque

    from tests.support.thin_sdk import output
    from tests.thin.test_analyst_report_access import ContextModel, tool_data
    from tests.thin.test_direct_text_handoff import direct_config
    from portfolio_management.followup import load_parent_review

    parent, first = await parent_review(tmp_path / "first", record_count=700)
    original_packet = load_parent_review(parent)["packet"]
    before = hashes(parent)
    lead = ContextModel(output("consult_expert"), {"text": "Explanation delivered"})
    expert = ContextModel(
        output("evidence_read", record_ref="evidence:quote-old"),
        {"text": "Prior evidence was dated October29, not a new quote."},
    )
    models = deque([lead, expert])
    result = await run_team(
        TeamRequest(
            question="Why did you say that?",
            mode="education",
            parent_review=str(parent),
            review_kind="explanation",
        ),
        config=direct_config(tmp_path / "followup"),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.completion_reason == "complete" and not models
    for model in (lead, expert):
        prompt_packet = json.loads(
            model.calls[0]["messages"][0]["content"][0]["text"].split(
                "Prior review (private ordinary dated data; not current observations):\n", 1
            )[1]
        )
        assert "artifact_hashes" not in prompt_packet
        assert "record_hashes" not in prompt_packet
        assert "source_dates" not in prompt_packet
        assert prompt_packet["prior_report"] == original_packet["prior_report"]
        assert len(model.system_prompt) < 20000
    assert json.loads(Path(result.artifacts["parent_review"]).read_text()) == original_packet
    assert sum(row["scope"] == "public" for row in original_packet["record_hashes"].values()) == 700
    child = Path(result.artifacts["result"]).parent
    for directory in ("records", "private-records"):
        for source in (parent / directory).glob("*.json"):
            imported = child / directory / source.name
            assert json.loads(imported.read_text()) == json.loads(source.read_text())
    assert hashes(parent) == before
    assert "Quote from October29; not current." not in expert.system_prompt
    assert (
        "Quote from October29; not current." in expert.calls[0]["messages"][0]["content"][0]["text"]
    )
    assert "Owner-stated preference, not verified capacity." not in expert.system_prompt
    assert (
        expert.calls[0]["messages"][0]["content"][0]["text"].count(
            "Owner-stated preference, not verified capacity."
        )
        == 1
    )
    assert (
        tool_data(expert.calls[1])["data"]["record"]["source_timestamp"] == "2026-10-29T20:00:00Z"
    )
    assert "request_research" not in {t["name"] for t in expert.calls[0]["tools"]}
    assert result.lineage["review_session_id"] == first.run_id


def test_cli_explanation_does_not_require_unavailable_market_capture(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from portfolio_management import cli

    cfg = config(tmp_path).model_copy(
        update={"market_capture_manifest_path": tmp_path / "missing-capture.json"}
    )
    seen = []
    monkeypatch.setattr(cli, "load_team_config", lambda _: cfg)

    async def saved_explanation(request, **kwargs):
        seen.append(kwargs["deps"])
        return SimpleNamespace(
            report_markdown="Dated explanation",
            run_id="offline",
            completion_reason="complete",
            artifacts={},
        )

    monkeypatch.setattr(cli, "run_team", saved_explanation)
    code = cli.main(
        [
            "ask",
            "--question",
            "Explain",
            "--mode",
            "education",
            "--config",
            "unused",
            "--output",
            str(tmp_path),
            "--parent-review",
            str(tmp_path / "parent"),
            "--review-kind",
            "explanation",
        ]
    )
    assert code == 0
    assert seen[0].market_provider is None and seen[0].research_backend is None


@pytest.mark.parametrize(
    "damage",
    [
        "report",
        "record",
        "request",
        "checkpoint_missing",
        "directory_symlink",
        "file_symlink",
        "missing",
    ],
)
async def test_invalid_parent_fails_before_model_or_new_run(tmp_path, damage):
    parent, _ = await parent_review(tmp_path / "first")
    selected = parent
    if damage == "report":
        (parent / "report.md").write_text("altered report")
    if damage == "record":
        next((parent / "records").glob("*.json")).write_text("{}")
    if damage == "request":
        (parent / "request.json").write_text("{}")
    if damage == "checkpoint_missing":
        (parent / "review-checkpoint.json").unlink()
    if damage == "missing":
        selected = tmp_path / "absent"
    if damage == "directory_symlink":
        selected = tmp_path / "alias"
        selected.symlink_to(parent, target_is_directory=True)
    if damage == "file_symlink":
        external = tmp_path / "external"
        external.write_bytes((parent / "report.md").read_bytes())
        (parent / "report.md").unlink()
        (parent / "report.md").symlink_to(external)

    def never(*args):
        pytest.fail("Invalid parent reached inference")

    with pytest.raises((ValueError, OSError)):
        await run_team(
            TeamRequest(
                question="Explain",
                mode="education",
                parent_review=str(selected),
                review_kind="explanation",
            ),
            config=config(tmp_path / "new"),
            deps=TeamDependencies(model_factory=never, clock=fixed_clock),
        )
    assert not (tmp_path / "new").exists()
