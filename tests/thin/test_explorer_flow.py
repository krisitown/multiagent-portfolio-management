"""Offline SDK integration: discovery and holdings feed one intact expert context."""

import json
from collections import deque
from pathlib import Path

import pytest

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_direct_text_handoff import direct_config
from portfolio_management.team import run_team
from portfolio_management.types import TeamDependencies, TeamRequest


def explorer_config(tmp_path, **updates):
    from portfolio_management.config import TeamConfig

    values = direct_config(tmp_path).model_dump()
    values.update(
        explorer_enabled=True,
        deadline_seconds=10800,
        analyst_concurrency=5,
        max_expert_consultations=1,
        max_generations=1000,
    )
    values["model"].update(timeout_seconds=10800, max_output_tokens=65536)
    values.update(updates)
    return TeamConfig.model_validate(values)


class CapturingModel(ScriptModel):
    def __init__(self, *steps):
        super().__init__(*steps)
        self.systems = []

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.systems.append(system_prompt)
        async for event in super().stream(
            messages, tool_specs=tool_specs, system_prompt=system_prompt, **kwargs
        ):
            yield event


async def test_explorer_and_holdings_reach_one_holistic_expert(tmp_path):
    original = (
        "  Account as of 2026-10-31\r\nAAPL shares +12.500; short puts -2\r\n"
        "Margin ~ 1,234.56 USD (estimate)\r\n"
    )
    exploration = "Investigate MSFT opportunity. Source: https://example.org"
    lead = ScriptModel(
        output("explore", question="Find market context and candidates"),
        output(
            "research_batch",
            jobs=[
                {"job_id": "new", "question": "MSFT opportunity"},
                {"job_id": "held", "question": "AAPL holding"},
            ],
        ),
        output("consult_expert", question="One holistic review", job_ids=["new", "held"]),
        {"text": "  Original final report, including uncertainty.  "},
    )
    explorer = CapturingModel({"text": exploration})
    analysts = [
        CapturingModel({"text": "MSFT completed finding"}),
        CapturingModel({"text": "AAPL completed finding"}),
    ]
    expert = CapturingModel({"text": "Holistic daily assessment"})
    models = deque([lead, explorer, *analysts, expert])
    seen = []

    def factory(*args):
        model = models.popleft()
        seen.append(model)
        return model

    cfg = explorer_config(tmp_path)
    result = await run_team(
        TeamRequest(question="Research portfolio", mode="combined", portfolio_text=original),
        config=cfg,
        deps=TeamDependencies(model_factory=factory),
    )
    assert result.report_markdown == "  Original final report, including uncertainty.  "
    assert len(expert.calls) == 1
    review = json.loads(expert.calls[0]["messages"][-1]["content"][0]["text"])
    assert review["explorer_reports"][0]["summary"] == exploration
    assert [r["job_id"] for r in review["analyst_reports"]] == ["new", "held"]
    # JSON representation is decoded before checking exact source text.
    systems = expert.systems[0]
    assert original not in systems
    assert original in expert.calls[0]["messages"][0]["content"][0]["text"]
    assert expert.calls[0]["messages"][0]["content"][0]["text"].count(original) == 1
    assert original not in review["question"]
    for child in [explorer, *analysts]:
        assert original not in child.systems[0]
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert "explorer" in manifest["role_prompts"]


async def test_disabled_explorer_keeps_old_three_role_selection(tmp_path):
    lead = ScriptModel({"text": "Legacy replay"})
    result = await run_team(
        TeamRequest(question="Replay", mode="education"),
        config=direct_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: lead),
    )
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert "explorer" not in manifest["role_prompts"]
    assert "explore" not in {t["name"] for t in lead.calls[0]["tools"]}


async def test_enabled_explorer_missing_prompt_fails_before_inference(tmp_path):
    from importlib.resources import files

    from portfolio_management.config import PromptSet
    from portfolio_management.prompt_loader import PromptReadinessError

    # Absolute source paths remain within this selected copied bundle.
    fixture = Path(str(files("portfolio_management").joinpath("prompts/development")))
    import shutil

    shutil.copytree(fixture, tmp_path / "bundle")
    manifest_path = tmp_path / "bundle/manifest.json"
    data = json.loads(manifest_path.read_text())
    del data["roles"]["explorer"]
    data["shared"] = {}
    data["modules"] = {}
    manifest_path.write_text(json.dumps(data))
    cfg = explorer_config(
        tmp_path / "runs",
        prompts=PromptSet(mode="development", manifest_path=manifest_path),
    )
    called = []
    with pytest.raises(PromptReadinessError, match="explorer"):
        await run_team(
            TeamRequest(question="No inference", mode="education"),
            config=cfg,
            deps=TeamDependencies(model_factory=lambda *_: called.append(True)),
        )
    assert called == []
    assert not (tmp_path / "runs").exists()


async def test_explorer_failure_is_original_explicit_expert_context(tmp_path):
    lead = ScriptModel(
        output("explore", question="Connected discovery"),
        output("consult_expert", question="Disclose missing research"),
        {"text": "Research unavailable; no invented findings"},
    )
    explorer = ScriptModel(RuntimeError("fixture transport failure"))
    expert = ScriptModel({"text": "Missing discovery disclosed"})
    models = deque([lead, explorer, expert])
    cfg = explorer_config(tmp_path)
    await run_team(
        TeamRequest(question="Review", mode="combined"),
        config=cfg,
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    review = json.loads(expert.calls[0]["messages"][-1]["content"][0]["text"])
    assert review["explorer_reports"][0]["outcome"] == "failed"
    assert review["explorer_reports"][0]["summary"] == ""
    assert review["explorer_reports"][0]["error"] == "Explorer failed (RuntimeError)"


async def test_overlapping_explorers_have_distinct_ids_and_private_child_requests(tmp_path):
    import asyncio

    from tests.thin.test_entrypoint import snapshot
    from portfolio_management.types import ResearchResult, TeamResult

    both_started = asyncio.Event()
    started = 0
    seen = []

    class Role:
        def __init__(self, role, ctx):
            self.role, self.ctx = role, ctx

        async def invoke_async(self, question):
            nonlocal started
            if self.role == "team_lead":
                results = await asyncio.gather(
                    self.ctx.tools["explore"]("first"),
                    self.ctx.tools["explore"]("second"),
                )
                assert len({r["job_id"] for r in results}) == 2
                await self.ctx.tools["research_batch"](
                    jobs=[{"job_id": "held", "question": "Research AAPL"}]
                )
                return TeamResult(summary="Original concurrent report")
            seen.append(self.ctx)
            assert self.ctx.request.portfolio is None
            assert self.ctx.request.portfolio_text is None
            if self.role == "explorer":
                started += 1
                if started == 2:
                    both_started.set()
                await both_started.wait()
            return ResearchResult(job_id="model-id", outcome="success", summary=question)

    cfg = explorer_config(tmp_path, child_output_mode="typed")
    result = await run_team(
        TeamRequest(
            question="Research", mode="combined", portfolio=snapshot(), portfolio_text="private"
        ),
        config=cfg,
        deps=TeamDependencies(role_factory=lambda role, ctx: Role(role, ctx)),
    )
    assert result.completion_reason == "complete"
    assert result.summary == "Original concurrent report"
    rows = json.loads(Path(result.artifacts["explorer"]).read_text())
    assert {r["job_id"] for r in rows} == {"explorer:1", "explorer:2"}
    assert len(seen) == 3
    trace = [json.loads(line) for line in Path(result.artifacts["trace"]).read_text().splitlines()]
    for event in ["explorer_started", "explorer_completed"]:
        assert {r["invocation_id"] for r in trace if r["event"] == event} == {
            "explorer:1",
            "explorer:2",
        }


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
async def test_stopped_explorer_saves_cancelled_outcome_and_cleans_child(tmp_path, stop):
    import asyncio

    from portfolio_management.types import TeamResult

    entered = asyncio.Event()
    cleaned = asyncio.Event()

    class Role:
        def __init__(self, role, ctx):
            self.role, self.ctx = role, ctx

        async def invoke_async(self, question):
            if self.role == "team_lead":
                await self.ctx.tools["explore"]("Blocked research")
                return TeamResult(summary="unreachable")
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

    cfg = explorer_config(
        tmp_path,
        child_output_mode="typed",
        deadline_seconds=0.2 if stop == "deadline" else 30,
    )
    task = asyncio.create_task(
        run_team(
            TeamRequest(question="Research", mode="education"),
            config=cfg,
            deps=TeamDependencies(role_factory=lambda role, ctx: Role(role, ctx)),
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=2)
    if stop == "cancel":
        task.cancel()
    result = await asyncio.wait_for(task, timeout=2)
    assert cleaned.is_set()
    assert result.completion_reason == ("cancelled" if stop == "cancel" else "deadline")
    rows = json.loads(Path(result.artifacts["explorer"]).read_text())
    assert len(rows) == 1
    assert rows[0]["job_id"] == "explorer:1"
    assert rows[0]["outcome"] == "cancelled"
    assert rows[0]["summary"] == ""
    trace = [json.loads(line) for line in Path(result.artifacts["trace"]).read_text().splitlines()]
    terminal = [r for r in trace if r["event"] == "explorer_completed"]
    assert len(terminal) == 1
    assert terminal[0]["invocation_id"] == "explorer:1"
    assert terminal[0]["outcome"] == "cancelled"


async def test_generic_request_supplies_holdings_to_lead_before_research(tmp_path):
    original = "As of 2026-10-31\r\nROP shares +17.125; reported margin unknown.\n"

    class HoldingsLead(CapturingModel):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            if not self.calls:
                assert messages[-1]["content"][0]["text"] == "Review my portfolio"
                assert original not in system_prompt
                assert messages[0]["content"][0]["text"].count(original) == 1
                assert "consult_expert() with no arguments" in system_prompt
                assert "Do not select job IDs" in system_prompt
                # Derive the public research assignment from actual supplied holdings.
                supplied = messages[0]["content"][0]["text"].split(
                    "Original readable portfolio (data):\n"
                )[1]
                symbol = supplied.splitlines()[1].split()[0]
                self.steps = [
                    output("explore", question="Public market context"),
                    output(
                        "research_batch",
                        jobs=[{"job_id": "holding", "question": f"Research {symbol} holding"}],
                    ),
                    output("consult_expert", question="Holistic review", job_ids=["holding"]),
                    {"text": "Original holdings review"},
                ]
            async for event in super().stream(
                messages, tool_specs=tool_specs, system_prompt=system_prompt, **kwargs
            ):
                yield event

    lead = HoldingsLead()
    explorer = CapturingModel({"text": "Public market context; account holdings unavailable"})
    analyst = CapturingModel({"text": "ROP holding finding"})
    expert = CapturingModel({"text": "One holistic review"})
    models = deque([lead, explorer, analyst, expert])
    result = await run_team(
        TeamRequest(question="Review my portfolio", mode="combined", portfolio_text=original),
        config=explorer_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.report_markdown == "Original holdings review"
    assignment = json.loads(analyst.calls[0]["messages"][0]["content"][0]["text"])
    assert assignment["question"] == "Research ROP holding"
    assert original not in analyst.systems[0]
    assert original not in explorer.systems[0]
    assert "ROP" not in explorer.systems[0]
    assert original not in expert.systems[0]
    assert expert.calls[0]["messages"][0]["content"][0]["text"].count(original) == 1
    assert original not in json.dumps(expert.calls[0]["messages"])
