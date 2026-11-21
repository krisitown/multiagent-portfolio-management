"""Explorer sampling is per-role request configuration, never a serving mutation."""

from collections import deque

import pytest

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_explorer_flow import explorer_config
from wheel_team.config import TeamConfig
from wheel_team.team import run_team
from wheel_team.types import TeamDependencies, TeamRequest


@pytest.mark.parametrize("baseline", [None, 0.3])
@pytest.mark.parametrize("override", [0.0, 1.2])
async def test_only_explorer_model_factory_receives_temperature_override(
    tmp_path, baseline, override
):
    models = deque(
        [
            ScriptModel(
                output("explore", question="Public discovery"),
                output("research_batch", jobs=[{"job_id": "public", "question": "MSFT"}]),
                output("consult_expert", question="Holistic review", job_ids=["public"]),
                {"text": "Done"},
            ),
            ScriptModel({"text": "Discovery"}),
            ScriptModel({"text": "Finding"}),
            ScriptModel({"text": "Assessment"}),
        ]
    )
    selected = []

    def factory(config, *_):
        selected.append(config)
        return models.popleft()

    original = explorer_config(tmp_path)
    config = TeamConfig.model_validate(
        original.model_dump()
        | {
            "explorer_temperature": override,
            "model": original.model.model_dump() | {"temperature": baseline},
        }
    )
    await run_team(
        TeamRequest(question="Review", mode="combined"),
        config=config,
        deps=TeamDependencies(model_factory=factory),
    )
    assert [item.temperature for item in selected] == [baseline, override, baseline, baseline]
    assert config.model.temperature == baseline
