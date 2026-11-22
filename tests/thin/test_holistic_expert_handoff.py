"""The SDK host owns complete original holistic handoff; no lead retelling."""

import json
from collections import deque

import pytest

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_analyst_report_access import ContextModel, expert_data, tool_data
from tests.thin.test_direct_text_handoff import CutoffModel, direct_config
from tests.thin.test_explorer_flow import explorer_config
from portfolio_management.team import run_team
from portfolio_management.types import TeamDependencies, TeamRequest


@pytest.mark.parametrize("legacy_extras", [False, True])
async def test_all_originals_and_owner_request_are_automatic(tmp_path, legacy_extras):
    owner = "Original owner request α\nInspect the complete book."
    portfolio = "PRIVATE ORIGINAL PORTFOLIO α\nSigned terms and dated basis."
    first = "  Original α\r\nFirst report, not a lead paraphrase.  "
    second = "  Original β\nSecond completed batch report.  "
    args = (
        {
            "question": "DISTORTED LEAD RETELLING",
            "job_ids": ["explorer:1", "first"],
            "idea_refs": ["idea:absent"],
            "evidence_refs": ["evidence:absent"],
        }
        if legacy_extras
        else {}
    )
    lead = ScriptModel(
        output("explore", question="Discover"),
        output("research_batch", jobs=[{"job_id": "first", "question": "Inspect"}]),
        output("research_batch", jobs=[{"job_id": "omitted-before", "question": "Inspect"}]),
        output("consult_expert", **args),
        output("consult_expert"),
        {"text": "Complete"},
    )
    expert = ContextModel({"text": "One holistic review"})
    models = deque(
        [
            lead,
            ScriptModel({"text": "Explorer original"}),
            ScriptModel({"text": first}),
            CutoffModel({"text": second}),
            expert,
        ]
    )
    cfg = explorer_config(tmp_path)
    result = await run_team(
        TeamRequest(question=owner, mode="combined", portfolio_text=portfolio),
        config=cfg,
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.summary == "Complete"
    assert not models
    data = expert_data(expert)
    assert data["question"] == owner
    assert data["analyst_reports"] == tool_data(lead.calls[2]) + tool_data(lead.calls[3])
    assert [r["summary"] for r in data["analyst_reports"]] == [first, second]
    assert data["analyst_reports"][1]["outcome"] == "partial"
    assert data["explorer_reports"][0]["summary"] == "Explorer original"
    assert "DISTORTED LEAD RETELLING" not in json.dumps(data)
    assert "selected_records" not in data
    assert portfolio not in json.dumps(data)
    assert portfolio not in expert.system_prompt
    assert expert.calls[0]["messages"][0]["content"][0]["text"].count(portfolio) == 1
    assert "Select relevant" not in expert.system_prompt
    spec = next(t for t in lead.calls[0]["tools"] if t["name"] == "consult_expert")
    assert spec["inputSchema"]["json"].get("properties", {}) == {}
    assert not spec["inputSchema"]["json"].get("required")
    assert tool_data(lead.calls[5])["error"] == "Expert consultation limit reached"


async def test_duplicate_job_ids_and_failed_envelopes_remain_original(tmp_path):
    lead = ScriptModel(
        output("research_batch", jobs=[{"job_id": "reused", "question": "First"}]),
        output(
            "research_batch",
            jobs=[
                {"job_id": "reused", "question": "Second"},
                {"job_id": "empty", "question": "Empty"},
            ],
        ),
        output("consult_expert"),
        {"text": "Complete"},
    )
    expert = ScriptModel({"text": "Review"})
    models = deque(
        [
            lead,
            ScriptModel({"text": "Same original"}),
            ScriptModel({"text": "Same original"}),
            ScriptModel({"text": ""}),
            expert,
        ]
    )
    await run_team(
        TeamRequest(question="Owner request", mode="education"),
        config=direct_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    reports = expert_data(expert)["analyst_reports"]
    assert len(reports) == 3
    assert [r["job_id"] for r in reports] == ["reused", "reused", "empty"]
    assert reports[-1]["outcome"] == "failed"
    assert reports[-1]["error"] == "Analyst failed (ValueError)"
    assert reports[-1]["summary"] == ""
