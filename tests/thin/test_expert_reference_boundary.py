"""Holistic review has no model-selected reference prerequisite."""

from collections import deque

import pytest

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_analyst_report_access import expert_data
from tests.thin.test_direct_text_handoff import direct_config
from portfolio_management.team import run_team
from portfolio_management.types import TeamDependencies, TeamRequest
from portfolio_management.vault import EvidenceStore


async def test_holistic_review_does_not_require_reference_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(EvidenceStore, "get", lambda *_: pytest.fail("Handoff read a record"))
    lead = ScriptModel(output("consult_expert"), {"text": "Delivered"})
    expert = ScriptModel({"text": "Original assessment"})
    models = deque([lead, expert])
    result = await run_team(
        TeamRequest(question="Original owner task", mode="education"),
        config=direct_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.summary == "Delivered"
    assert expert_data(expert) == {
        "question": "Original owner task",
        "analyst_reports": [],
        "explorer_reports": [],
    }
