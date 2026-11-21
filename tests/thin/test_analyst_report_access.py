"""Complete original handoffs reach experts inline through the real offline SDK."""

import json
from collections import deque
from pathlib import Path

import pytest

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_direct_text_handoff import CutoffModel, direct_config
from tests.thin.test_entrypoint import snapshot
from wheel_team.team import run_team
from wheel_team.types import TeamDependencies, TeamRequest


class ContextModel(ScriptModel):
    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.system_prompt = system_prompt
        async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
            yield event


def tool_data(call):
    return json.loads(call["messages"][-1]["content"][0]["toolResult"]["content"][0]["text"])


def expert_data(model):
    return json.loads(
        next(
            m["content"][0]["text"]
            for m in model.calls[0]["messages"]
            if m["role"] == "user"
            and not m["content"][0]["text"].startswith("Caller-supplied private task data:")
        )
    )


@pytest.mark.parametrize("artifact_write_fails", [False, True])
async def test_sdk_expert_receives_all_original_reports_without_reader_calls(
    tmp_path, monkeypatch, artifact_write_fails
):
    # Catches missing automatic injection or altered originals/status,
    # or requiring passive files/reader tools before ordinary expert delivery.
    import wheel_team.team as team

    failures = []
    original_write = team._write_json

    def write(path, value):
        if (
            artifact_write_fails
            and value
            and path.name in {"research-results.json", "expert-results.json"}
        ):
            failures.append(path.name)
            raise OSError("offline disk failure")
        return original_write(path, value)

    monkeypatch.setattr(team, "_write_json", write)
    narrative = "  ## Original α\nhttps://example.org/a?x=1&y=2 [evidence:raw-id]  "
    partial = "  Exact unfinished narrative\nUSD limitation  "
    lead = ScriptModel(
        output(
            "research_batch",
            jobs=[
                {"job_id": name, "question": "Inspect"} for name in ["complete", "partial", "empty"]
            ],
        ),
        output("consult_expert", question="Review", job_ids=["complete", "partial", "empty"]),
        output("consult_expert", question="Independent check"),
        {"text": "Expert text delivered"},
    )
    expert = ScriptModel({"text": "  Ordinary expert assessment\nwith original limitations  "})
    independent = ScriptModel({"text": "Independent"})
    models = deque(
        [
            lead,
            ScriptModel({"text": narrative}),
            CutoffModel({"text": partial}),
            ScriptModel({"text": ""}),
            expert,
            independent,
        ]
    )
    result = await run_team(
        TeamRequest(question="Offline transport", mode="education"),
        config=direct_config(tmp_path).model_copy(update={"max_expert_consultations": 2}),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.summary == "Expert text delivered"
    assert not models
    spec = next(t for t in lead.calls[0]["tools"] if t["name"] == "consult_expert")
    # The holistic consultation has no model-selected handoff fields.
    assert spec["inputSchema"]["json"].get("properties", {}) == {}
    selected = expert_data(expert)
    assert selected["question"] == "Offline transport"
    assert selected["analyst_reports"] == [
        {
            "job_id": "complete",
            "outcome": "success",
            "summary": narrative,
            "idea_refs": [],
            "evidence_refs": [],
            "findings": [],
            "error": None,
        },
        {
            "job_id": "partial",
            "outcome": "partial",
            "summary": partial,
            "idea_refs": [],
            "evidence_refs": [],
            "findings": [],
            "error": "Analyst text cut off at max_tokens",
        },
        {
            "job_id": "empty",
            "outcome": "failed",
            "summary": "",
            "idea_refs": [],
            "evidence_refs": [],
            "findings": [],
            "error": "Analyst failed (ValueError)",
        },
    ]
    assert selected["analyst_reports"] == tool_data(lead.calls[1])
    assert expert_data(independent)["analyst_reports"] == selected["analyst_reports"]
    review = tool_data(lead.calls[2])
    assert review["assessment"] == "  Ordinary expert assessment\nwith original limitations  "
    assert review["outcome"] == "success"
    assert len(expert.calls) == 1
    assert failures == (
        ["research-results.json"] * 3 + ["expert-results.json"] * 2 if artifact_write_fails else []
    )
    assert "Original α" not in Path(result.artifacts["report"]).read_text()


async def test_legacy_selections_cannot_drop_or_duplicate_originals(tmp_path):
    # Legacy selectors cannot read a path or drop any completed envelope.
    private = tmp_path / "private.json"
    private.write_text('{"summary":"must not read"}')
    lead = ScriptModel(
        output("research_batch", jobs=[{"job_id": "reused", "question": "First"}]),
        output(
            "research_batch",
            jobs=[
                {"job_id": "reused", "question": "Again"},
                {"job_id": "valid", "question": "Valid"},
            ],
        ),
        output(
            "consult_expert",
            question="Select",
            job_ids=["absent", "valid", str(private), "valid", "reused"],
        ),
        {"text": "Selected"},
    )
    expert = ScriptModel({"text": "Reviewed"})
    models = deque(
        [
            lead,
            ScriptModel({"text": "First"}),
            ScriptModel({"text": "Second"}),
            ScriptModel({"text": "Original"}),
            expert,
        ]
    )
    await run_team(
        TeamRequest(question="Inspect", mode="education"),
        config=direct_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    rows = expert_data(expert)["analyst_reports"]
    assert [row["job_id"] for row in rows] == ["reused", "reused", "valid"]
    assert [row["summary"] for row in rows] == ["First", "Second", "Original"]
    assert "must not read" not in json.dumps(rows)


async def test_owner_request_original_narrative_and_portfolio_are_inline(tmp_path, monkeypatch):
    # Catches altering original reports or requiring handoff files,
    # and automatically expanding private raw source history into the expert context.
    from tests.thin.test_research import Transport, api
    from tests.thin.test_research import config as research_config
    from tests.thin.test_vault import evidence
    from wheel_team.contracts import EvidenceRecord, MarketResult
    from wheel_team.vault import EvidenceStore

    market_source = EvidenceRecord.model_validate(
        evidence(
            source="yahoo-fundamentals",
            source_url="https://issuer.example/filing",
            content={
                "capability": "fundamentals",
                "data": {
                    "financialCurrency": "USD",
                    "totalRevenue": "100.001",
                    "non_selected_original": "x" * 40000,
                    "conflicts": ["issuer basis differs from vendor basis"],
                },
            },
        )
    ).model_dump(mode="json")

    class Provider:
        def capabilities(self):
            return ["fundamentals"]

        async def query(self, request):
            return MarketResult(
                query_id=request.query_id,
                status="success",
                complete=False,
                evidence=[market_source],
            )

    adapter, _ = api()
    transport = Transport(
        {
            "results": [
                {
                    "url": "https://issuer.example/annual",
                    "title": "Exact report",
                    "raw_content": "  Source text with limitations α  ",
                    "published_date": "2026-10-29T16:00:00-04:00",
                }
            ]
        }
    )
    backend = adapter.HermesResearchBackend(research_config(adapter), transport=transport)
    excerpt = (
        "  Review this explicit excerpt α\nhttps://issuer.example/annual\n"
        "2026-10-29T16:00:00-04:00: exact original limitations  "
    )
    lead = ContextModel(
        output("research_batch", jobs=[{"job_id": "source", "question": "Inspect"}]),
        output("consult_expert", question=excerpt, job_ids=["source"]),
        {"text": "Lead chooses only this summary"},
    )
    analyst = ContextModel(
        output("market_query", query={"query_id": "q", "capability": "fundamentals"}),
        output("fetch_sources", urls=["https://issuer.example/annual"]),
        {"text": "  Exact selected narrative [evidence:original] with original limitations  "},
    )
    expert = ContextModel({"text": "Source review"})
    models = deque([lead, analyst, expert])
    # No handoff may require evidence reader/store.get; tool writes remain real.
    monkeypatch.setattr(EvidenceStore, "get", lambda *_: pytest.fail("handoff read a file"))
    portfolio_text = "  Account α\nAAPL signed quantity: -2.50\nReported margin: unknown  "
    request = TeamRequest(
        question="Inspect", mode="education", portfolio=snapshot(), portfolio_text=portfolio_text
    )
    result = await run_team(
        request,
        config=direct_config(tmp_path),
        deps=TeamDependencies(
            model_factory=lambda *_: models.popleft(),
            market_provider=Provider(),
            research_backend=backend,
        ),
    )
    assert result.summary == "Lead chooses only this summary"
    original = tool_data(lead.calls[1])[0]
    assert original["outcome"] == "success"
    assert (
        original["summary"]
        == "  Exact selected narrative [evidence:original] with original limitations  "
    )
    assert expert_data(expert)["analyst_reports"] == [original]
    selected = expert_data(expert)
    assert selected["question"] == request.question
    assert excerpt not in json.dumps(selected)
    assert "selected_records" not in selected
    assert "analyst_reports_catalog" not in selected
    assert "non_selected_original" not in json.dumps(selected)
    assert "Source text with limitations" not in json.dumps(selected)
    assert "source_context" not in original
    records = list((Path(result.artifacts["research"]).parent / "records").glob("*.json"))
    assert any("non_selected_original" in path.read_text() for path in records)
    assert any("Source text with limitations" in path.read_text() for path in records)
    marker = "\n\nOriginal readable portfolio (data):\n"
    for model in [lead, analyst, expert]:
        runtime_text = model.system_prompt.split("Run context (data):\n")[1].split(marker)[0]
        runtime = json.loads(runtime_text)
        assert "portfolio_snapshot" not in runtime
        assert portfolio_text not in runtime_text
        if model in [lead, expert]:
            assert (
                model.calls[0]["messages"][0]["content"][0]["text"].split(marker)[1]
                == portfolio_text
            )
            assert model.calls[0]["messages"][0]["content"][0]["text"].count(marker) == 1
            assert marker not in model.system_prompt
        else:
            assert marker not in model.system_prompt
            assert portfolio_text not in model.system_prompt
    assert "non_selected_original" not in result.summary
    assert "non_selected_original" not in Path(result.artifacts["report"]).read_text()


async def test_stopped_run_keeps_dossiers_private(tmp_path):
    # Catches unconditional promotion of all child text to the user's partial summary.
    lead = ScriptModel(
        output("research_batch", jobs=[{"job_id": "private", "question": "Inspect"}]),
        RuntimeError("lead failed"),
    )
    models = deque([lead, ScriptModel({"text": "Privately retained analyst dossier"})])
    result = await run_team(
        TeamRequest(question="Inspect", mode="education"),
        config=direct_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.completion_reason == "failed"
    assert "Privately retained analyst dossier" not in result.summary
    assert "Privately retained analyst dossier" not in Path(result.artifacts["report"]).read_text()
    assert "Privately retained analyst dossier" in Path(result.artifacts["research"]).read_text()


@pytest.mark.parametrize(
    "role,expected",
    [
        ("team_lead", {"research_batch", "consult_expert"}),
        (
            "analyst",
            {
                "finance_calculate",
                "knowledge_read",
                "market_query",
                "public_search",
                "fetch_sources",
                "evidence_read",
                "evidence_find",
            },
        ),
        (
            "wheel_expert",
            {
                "finance_calculate",
                "knowledge_read",
                "evidence_read",
                "evidence_find",
                "request_research",
            },
        ),
    ],
)
async def test_role_catalogs_keep_real_tools_and_hide_internal_handoff_tools(
    tmp_path, role, expected
):
    # Public/private reader bindings are tested separately; publications remain forbidden.
    from tests.thin.test_agents import context
    from wheel_team.agents.base import build_role

    forbidden = {
        "publish_idea",
        "publish_quality",
        "publish_review",
        "analyst_reports_read",
        "portfolio_read",
    }

    async def operation(value: str = "") -> dict:
        return {"value": value}

    model = ScriptModel({"text": "Catalog inspected"})
    agent = build_role(
        role, context(tmp_path, role, model, {name: operation for name in expected | forbidden})
    )
    await agent.invoke_async("Inspect")
    assert {tool["name"] for tool in model.calls[0]["tools"]} == expected


async def test_automatic_reports_are_run_local_and_include_every_current_original(tmp_path):
    # All current originals are automatic; previous-run narratives never leak.
    first_lead = ScriptModel(
        output("research_batch", jobs=[{"job_id": "prior", "question": "Inspect"}]),
        {"text": "First run"},
    )
    queue = deque([first_lead, ScriptModel({"text": "Previous private narrative"})])
    await run_team(
        TeamRequest(question="Inspect", mode="education"),
        config=direct_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: queue.popleft()),
    )
    lead = ScriptModel(
        output(
            "research_batch",
            jobs=[{"job_id": name, "question": "Inspect"} for name in ["selected", "unselected"]],
        ),
        output("consult_expert", question="Review", job_ids=["prior", "selected"]),
        {"text": "Second run"},
    )
    original = "  Exact selected citation [source:α]  " + "long original text " * 4000
    expert = ScriptModel({"text": "Review delivered"})
    queue.extend(
        [
            lead,
            ScriptModel({"text": original}),
            ScriptModel({"text": "Unselected private narrative"}),
            expert,
        ]
    )
    await run_team(
        TeamRequest(question="Inspect", mode="education"),
        config=direct_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: queue.popleft()),
    )
    selected = expert_data(expert)
    assert [row["job_id"] for row in selected["analyst_reports"]] == ["selected", "unselected"]
    assert selected["analyst_reports"][0]["summary"] == original
    assert selected["analyst_reports"][1]["summary"] == "Unselected private narrative"
    assert "Previous private narrative" not in json.dumps(selected)
    assert "analyst_reports_catalog" not in selected
