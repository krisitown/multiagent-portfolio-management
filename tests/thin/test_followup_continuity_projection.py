"""Continuity receives bounded prior context without rewriting its saved originals."""

import json
from collections import deque
from pathlib import Path

import pytest

from tests.support.thin_sdk import output
from tests.thin.test_continuity_handoff import cfg
from tests.thin.test_fact_check_team import Capture
from tests.thin.test_followup import hashes, parent_review
from wheel_team.followup import load_parent_review
from wheel_team.team import run_team
from wheel_team.types import TeamDependencies, TeamRequest


@pytest.mark.parametrize("access", ["read", "write"])
async def test_continuity_projects_large_parent_only_at_model_boundary(tmp_path, access):
    # Catches reintroducing audit inventories into the model request, or shrinking
    # the persisted original instead of making a separate model-facing view.
    parent, _ = await parent_review(tmp_path / "parent", record_count=700)
    before = hashes(parent)
    original = load_parent_review(parent)["packet"]
    original_text = json.dumps(original, ensure_ascii=False)
    assert len(original["record_hashes"]) >= 700
    assert len(original["source_dates"]) >= 700
    assert len(original_text) > 200_000

    lead = Capture(
        output("consult_continuity", message="Review the dated originals", access=access),
        {"text": "Dated discussion retained"},
    )
    memory = Capture({"text": "Original assessment remains dated"})
    models = deque([lead, memory])
    config = cfg(tmp_path)
    result = await run_team(
        TeamRequest(
            question="Explain the prior assessment",
            mode="education",
            parent_review=str(parent),
            review_kind="explanation",
        ),
        config=config,
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert not models and result.completion_reason == "complete"

    # These remain the complete audit originals, independent of what was sent to
    # the model. The source is a large single-line JSON file, so inspect its bytes
    # rather than implying that one bounded continuity_read can return it all.
    assert hashes(parent) == before
    assert json.loads(Path(result.artifacts["parent_review"]).read_text()) == original
    consultation = json.loads(Path(result.artifacts["continuity"]).read_text())[0]
    saved_prior = consultation["input"]["originals"]["prior_review"]
    assert saved_prior["text"] == original_text
    source_ref = saved_prior["source_ref"]
    assert source_ref is not None
    assert (config.continuity.root / source_ref).read_bytes() == original_text.encode("utf-8")

    model_text = memory.calls[0]["messages"][0]["content"][0]["text"]
    assert len(model_text) < 20_000
    model_packet = json.loads(model_text)
    model_prior = model_packet["originals"]["prior_review"]
    assert model_prior["source_ref"] == source_ref
    projected = json.loads(model_prior["text"])
    inventories = {"artifact_hashes", "record_hashes", "source_dates"}
    assert not inventories & projected.keys()
    for name, value in original.items():
        if name not in inventories:
            assert projected[name] == value
    for name, value in consultation["input"]["originals"].items():
        if name != "prior_review":
            assert model_packet["originals"][name] == value
    tools = {tool["name"] for tool in memory.calls[0]["tools"]}
    assert "continuity_read" in tools
    assert not {"evidence_read", "evidence_find"} & tools
