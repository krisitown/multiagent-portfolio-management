"""Offline actual SDK role evaluations retain explicitly selected role sampling."""

import json
from pathlib import Path

import pytest

from tests.support.thin_fakes import fixed_clock
from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_role_isolation import model_config
from wheel_team.evaluation.thin_runner import run_case
from wheel_team.types import TeamDependencies


@pytest.mark.parametrize(
    "role,override,expected",
    [
        ("explorer", 0.25, 0.25),
        ("explorer", 0.0, 0.0),
        ("explorer", None, 0.8),
        ("analyst", 0.25, 0.8),
        ("wheel_expert", 0.25, 0.8),
        ("team_lead", 0.25, 0.8),
    ],
)
async def test_role_evaluation_applies_only_explicit_explorer_temperature(
    tmp_path, role, override, expected
):
    cfg = model_config(tmp_path)
    cfg = cfg.model_copy(
        update={
            "model": cfg.model.model_copy(update={"temperature": 0.8}),
            "explorer_temperature": override,
        }
    )
    final = {
        "team_lead": output("TeamResult", summary="Synthetic result"),
        "wheel_expert": output("ExpertResult", assessment="Synthetic result"),
    }.get(role, output("ResearchResult", job_id="synthetic", outcome="success"))
    model = ScriptModel(final)
    seen = []

    def factory(model_config, *_):
        seen.append(model_config)
        return model

    summary = await run_case(
        {"case_id": "sampling", "role": role, "question": "Offline sampling probe"},
        cfg,
        TeamDependencies(model_factory=factory, clock=fixed_clock),
        mode="role",
    )
    assert summary["metrics"]["model_calls"] == 1
    assert seen[0].temperature == expected
    assert cfg.model.temperature == 0.8
    assert seen[0].model_dump(exclude={"temperature"}) == cfg.model.model_dump(
        exclude={"temperature"}
    )
    manifest = json.loads(Path(summary["artifacts"]["manifest"]).read_text())
    assert manifest["model_settings"]["temperature"] == expected
