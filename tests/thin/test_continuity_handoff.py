"""Real SDK handoffs preserve exact private ordinary text and capability scope."""

import json
from collections import deque
from pathlib import Path

from tests.support.thin_sdk import output
from tests.thin.test_analyst_report_access import expert_data
from tests.thin.test_fact_check_team import Capture, checked_config
from portfolio_management.config import ContinuityConfig, PromptSet
from portfolio_management.continuity import ContinuityFiles
from portfolio_management.team import run_team
from portfolio_management.types import TeamDependencies, TeamRequest


def cfg(tmp_path):
    return checked_config(tmp_path).model_copy(
        update={
            "continuity": ContinuityConfig(
                root=tmp_path / "durable", prompts=PromptSet.development()
            )
        }
    )


async def test_original_read_to_expert_and_original_assessment_to_maintenance(tmp_path):
    brief = "  Prior work α\r\nUnresolved; owner agreement unknown.  "
    original = " Exact original Expert α\r\nassessment  "
    lead = Capture(
        output("consult_continuity", message="Relevant history?"),
        output("research_batch", jobs=[{"job_id": "a", "question": "Public issuer fundamentals"}]),
        output("consult_expert"),
        output("consult_continuity", message="Preserve continuing work", access="write"),
        {"text": original},
    )
    retrieval = Capture(output("continuity_list"), {"text": brief})
    analyst = Capture({"text": "Public research"})
    expert = Capture(
        output(
            "fact_check", message="Prior work is unresolved", evidence_refs=["input:continuity:1"]
        ),
        {"text": original},
    )
    checker = Capture({"text": "The supplied brief says unresolved; no new observation."})
    maintenance = Capture(
        output(
            "continuity_write",
            path="vault/cases/a.md",
            content="Dated unresolved case; adoption unknown.",
            expected_revision=None,
        ),
        {"text": "Saved case; this maintenance text is not the read brief."},
    )
    models = deque([lead, retrieval, analyst, expert, checker, maintenance])
    result = await run_team(
        TeamRequest(
            question="Original owner question", mode="portfolio", portfolio_text="Private book"
        ),
        config=cfg(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert not models
    assert result.summary == original
    data = expert_data(expert)
    assert data["continuity_context"][0]["text"] == brief
    assert data["continuity_context"][0]["evidence_ref"] == "input:continuity:1"
    assert "maintenance text" not in json.dumps(data)
    packet = json.loads(maintenance.calls[0]["messages"][0]["content"][0]["text"])
    assert packet["originals"]["expert_assessments"][0]["text"] == original
    source = packet["originals"]["expert_assessments"][0]["source_ref"]
    assert ContinuityFiles(tmp_path / "durable").continuity_read(source)["content"] == original
    assert packet["originals"]["question"]["text"] == "Original owner question"
    assert packet["originals"]["portfolio"]["text"] == "Private book"
    for model in (expert, analyst):
        names = {t["name"] for t in model.calls[0]["tools"]}
        assert not any(n.startswith("continuity") or n == "consult_continuity" for n in names)
    assert brief not in analyst.system and "Private book" not in analyst.system
    assert (
        json.loads(checker.calls[0]["messages"][0]["content"][0]["text"])["evidence"][0]["body"][
            "text"
        ]
        == brief
    )
    assert "continuity_write" not in {t["name"] for t in retrieval.calls[0]["tools"]}
    outer = next(t for t in lead.calls[0]["tools"] if t["name"] == "consult_continuity")[
        "inputSchema"
    ]["json"]
    assert set(outer["properties"]) == {"message", "access"}
    assert not next(t for t in lead.calls[0]["tools"] if t["name"] == "consult_expert")[
        "inputSchema"
    ]["json"].get("properties")
    records = json.loads(Path(result.artifacts["continuity"]).read_text())
    assert records[0]["result"]["text"] == brief
    assert records[1]["result"]["writes"][0]["path"] == "vault/cases/a.md"
    actual = json.loads(Path(result.artifacts["expert_inputs"]).read_text())[0]
    assert actual["continuity_context"][0]["text"] == brief
    assert records[1]["tools"][0]["input"]["content"] == "Dated unresolved case; adoption unknown."


async def test_source_save_failure_is_explicit_and_does_not_block_assessment(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("disk")

    monkeypatch.setattr(ContinuityFiles, "save_source", fail)
    lead = Capture(
        output("consult_expert"),
        output("consult_continuity", message="Preserve", access="write"),
        {"text": "Original assessment"},
    )
    expert = Capture({"text": "Original assessment"})
    memory = Capture({"text": "Sources unsaved; no save claimed"})
    models = deque([lead, expert, memory])
    result = await run_team(
        TeamRequest(question="Owner", mode="portfolio"),
        config=cfg(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.summary == "Original assessment"
    packet = json.loads(memory.calls[0]["messages"][0]["content"][0]["text"])
    assert packet["originals"]["expert_assessments"][0]["text"] == "Original assessment"
    assert packet["originals"]["expert_assessments"][0]["source_ref"] is None
    assert packet["source_errors"]


async def test_partial_maintenance_retains_write_receipts(tmp_path):
    lead = Capture(
        output("consult_continuity", message="Preserve", access="write"),
        {"text": "Report survives"},
    )
    memory = Capture(
        output(
            "continuity_write",
            path="vault/partial.md",
            content="Saved before failure",
            expected_revision=None,
        ),
        RuntimeError("interrupted"),
    )
    models = deque([lead, memory])
    result = await run_team(
        TeamRequest(question="Owner", mode="portfolio"),
        config=cfg(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    record = json.loads(Path(result.artifacts["continuity"]).read_text())[0]
    assert result.summary == "Report survives"
    assert record["result"]["completed"] is False
    assert len(record["result"]["writes"]) == 1
    assert (
        ContinuityFiles(tmp_path / "durable").continuity_read("vault/partial.md")["content"]
        == "Saved before failure"
    )


async def test_supporting_sources_are_references_not_context_dump(tmp_path):
    long_body = "Unrelated public research " * 10000
    lead = Capture(
        output("consult_continuity", message="history"),
        output("research_batch", jobs=[{"job_id": "a", "question": "Public"}]),
        output("consult_expert"),
        output("consult_continuity", message="save", access="write"),
        {"text": "done"},
    )
    retrieval = Capture({"text": "Earlier brief"})
    analyst = Capture({"text": long_body})
    expert = Capture({"text": "Original judgment"})
    maintenance = Capture({"text": "No unnecessary source reads"})
    models = deque([lead, retrieval, analyst, expert, maintenance])
    await run_team(
        TeamRequest(question="Owner", mode="portfolio"),
        config=cfg(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    text = maintenance.calls[0]["messages"][0]["content"][0]["text"]
    packet = json.loads(text)
    assert long_body not in text and len(text) < 10000
    assert "previous_consultations" not in packet
    assert packet["unfinished_maintenance"] == []
    assert "analyst_reports" not in packet["originals"]
    ref = packet["supporting_sources"][0]["source_ref"]
    assert (tmp_path / "durable" / ref).read_bytes().decode("utf-8") == long_body


async def test_production_prompt_cannot_fallback_development(tmp_path):
    import pytest

    from portfolio_management.prompt_loader import PromptReadinessError

    config = cfg(tmp_path).model_copy(
        update={"continuity": ContinuityConfig(root=tmp_path / "vault")}
    )
    with pytest.raises(PromptReadinessError, match="Production prompt manifest"):
        await run_team(
            TeamRequest(question="Owner", mode="portfolio"),
            config=config,
            deps=TeamDependencies(model_factory=lambda *_: None),
        )


async def test_explanation_continuity_keeps_file_tools_without_research(tmp_path):
    initial = await run_team(
        TeamRequest(question="First", mode="portfolio"),
        config=cfg(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: Capture({"text": "Initial review"})),
    )
    lead = Capture(output("consult_continuity", message="Prior meaning"), {"text": "Explained"})
    memory = Capture(output("continuity_list"), {"text": "Prior context"})
    models = deque([lead, memory])
    result = await run_team(
        TeamRequest(
            question="Explain",
            mode="portfolio",
            parent_review=str(Path(initial.artifacts["result"]).parent),
            review_kind="explanation",
        ),
        config=cfg(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.summary == "Explained"
    assert {t["name"] for t in memory.calls[0]["tools"]} == {
        "continuity_list",
        "continuity_read",
        "continuity_search",
    }


async def test_outer_rejects_source_refs_and_native_file_errors_are_json(tmp_path):
    lead = Capture(
        output("consult_continuity", message="Invalid", source_refs=["private:invented"]),
        output("consult_continuity", message="Retrieve"),
        {"text": "Done"},
    )
    memory = Capture(
        output("continuity_read", path="vault/missing.md"), {"text": "Missing file acknowledged"}
    )
    models = deque([lead, memory])
    result = await run_team(
        TeamRequest(question="Owner", mode="portfolio"),
        config=cfg(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    first = lead.calls[1]["messages"][-1]["content"][0]["toolResult"]
    assert first["status"] == "error"
    native = memory.calls[1]["messages"][-1]["content"][0]["toolResult"]
    assert native["status"] == "error"
    assert native["content"][0]["json"]["error"]["code"] == "NOT_FOUND"
    assert len(json.loads(Path(result.artifacts["continuity"]).read_text())) == 1


async def test_interrupted_maintenance_is_offered_after_process_restart(tmp_path):
    first_lead = Capture(
        output("consult_expert"),
        output("consult_continuity", message="Preserve unresolved original work", access="write"),
        {"text": "First report"},
    )
    first_expert = Capture({"text": "Original assessment before interruption"})
    interrupted = Capture(
        output(
            "continuity_write",
            path="vault/saved.md",
            content="Saved before interruption",
            expected_revision=None,
        ),
        RuntimeError("crash"),
    )
    models = deque([first_lead, first_expert, interrupted])
    await run_team(
        TeamRequest(question="First owner request", mode="portfolio"),
        config=cfg(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    second_lead = Capture(
        output("consult_continuity", message="Reconcile continuing work", access="write"),
        {"text": "Second report"},
    )
    second_memory = Capture(
        output("continuity_read", path="vault/saved.md"),
        {"text": "Inspected actual saved case; acknowledged prior interruption"},
    )
    models = deque([second_lead, second_memory])
    await run_team(
        TeamRequest(question="Next owner request", mode="portfolio"),
        config=cfg(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    packet = json.loads(second_memory.calls[0]["messages"][0]["content"][0]["text"])
    pending = packet["unfinished_maintenance"]
    assert len(pending) == 1
    assert pending[0]["message"] == "Preserve unresolved original work"
    assert pending[0]["writes"][0]["path"] == "vault/saved.md"
    assert pending[0]["original_source_refs"]
    assert "First owner request" not in json.dumps(pending)
    state = ContinuityFiles(tmp_path / "durable").unfinished_maintenance()
    assert len(state) == 1  # Successful second invocation is not reported as unfinished.
    assert list((tmp_path / "durable/.maintenance-history").rglob("*.json"))
