"""Role overrides reach model construction without spreading to peer/research authors."""

import json
from collections import deque
from pathlib import Path

import pytest

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_fact_check_team import checked_config
from tests.thin.test_portfolio_council import council_config
from portfolio_management.config import TeamConfig
from portfolio_management.team import run_team
from portfolio_management.types import TeamDependencies, TeamRequest


@pytest.mark.parametrize("override", [None, "high", "xhigh"])
@pytest.mark.parametrize("child", ["analyst", "wheel_expert", "dalio", "buffett", "asymmetric"])
async def test_manager_and_all_checker_paths_have_independent_effort(tmp_path, override, child):
    checked = checked_config(tmp_path)
    cfg = council_config(tmp_path, fact_check=checked.fact_check, asymmetric_enabled=True)
    data = cfg.model_dump()
    data["model"].update(reasoning_effort="medium", max_output_tokens=8192)
    if override is not None:
        data["portfolio_manager_reasoning_effort"] = override
        data["fact_check"]["reasoning_effort"] = override
    cfg = TeamConfig.model_validate(data)
    if child == "analyst":
        delegation = output("request_research", jobs=[{"job_id": "a", "question": "Public facts"}])
    elif child == "asymmetric":
        delegation = output("consult_asymmetric", question="Public opportunity")
    else:
        delegation = output("consult_specialists", requests=[{"role": child, "question": "Review"}])
    models = deque(
        [
            ScriptModel(
                delegation, output("fact_check", message="Manager report"), {"text": "Final"}
            ),
            ScriptModel(output("fact_check", message="Author finding"), {"text": "Finding"}),
            ScriptModel({"text": "Author check"}),
            ScriptModel({"text": "Manager check"}),
        ]
    )
    configs = []

    def factory(config, *_):
        configs.append(config)
        return models.popleft()

    result = await run_team(
        TeamRequest(question="Review", mode="combined"),
        config=cfg,
        deps=TeamDependencies(model_factory=factory),
    )
    assert not models
    assert result.report_markdown == "Final"
    expected = override or "medium"
    assert [c.reasoning_effort for c in configs] == [expected, "medium", expected, expected]
    assert [c.max_output_tokens for c in configs] == [8192, 8192, 4096, 4096]
    assert cfg.model.reasoning_effort == "medium"

    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert manifest["model_settings"]["reasoning_effort"] == "medium"
    assert manifest["model_settings"]["portfolio_manager_reasoning_effort"] == expected
    assert manifest["fact_check"]["reasoning_effort"] == expected


async def test_manager_provider_override_does_not_reach_peer_or_checkers(tmp_path):
    checked = checked_config(tmp_path)
    cfg = council_config(tmp_path, fact_check=checked.fact_check)
    data = cfg.model_dump()
    data["model"].update(model_id="qwen", reasoning_effort="medium")
    data["portfolio_manager_model"] = {
        **data["model"],
        "model_id": "deepseek-flash",
        "reasoning_effort": "high",
    }
    data["fact_check"]["reasoning_effort"] = "xhigh"
    cfg = TeamConfig.model_validate(data)
    models = deque(
        [
            ScriptModel(
                output("consult_specialists", requests=[{"role": "dalio", "question": "Review"}]),
                output("fact_check", message="Manager report"),
                {"text": "Final"},
            ),
            ScriptModel(output("fact_check", message="Peer finding"), {"text": "Finding"}),
            ScriptModel({"text": "Peer check"}),
            ScriptModel({"text": "Manager check"}),
        ]
    )
    configs = []

    def factory(config, *_):
        configs.append(config)
        return models.popleft()

    result = await run_team(
        TeamRequest(question="Review", mode="combined"),
        config=cfg,
        deps=TeamDependencies(model_factory=factory),
    )
    assert result.report_markdown == "Final"
    assert [(c.model_id, c.reasoning_effort) for c in configs] == [
        ("deepseek-flash", "high"),
        ("qwen", "medium"),
        ("qwen", "xhigh"),
        ("qwen", "xhigh"),
    ]
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert manifest["model_id"] == "qwen"
    assert manifest["portfolio_manager_model"]["model_id"] == "deepseek-flash"
    assert manifest["model_settings"]["portfolio_manager_reasoning_effort"] == "high"


@pytest.mark.parametrize("child", ["wheel_expert", "dalio", "buffett"])
async def test_role_models_keep_research_and_checkers_on_base_model(tmp_path, child):
    checked = checked_config(tmp_path)
    cfg = council_config(tmp_path, fact_check=checked.fact_check)
    data = cfg.model_dump()
    data["model"].update(model_id="qwen", reasoning_effort="medium")
    deepseek = {**data["model"], "model_id": "deepseek-flash", "reasoning_effort": "high"}
    data["role_models"] = {role: deepseek for role in ["portfolio_manager", child]}
    data["portfolio_manager_model"] = {**deepseek, "model_id": "legacy-manager"}
    data["fact_check"]["reasoning_effort"] = "xhigh"
    cfg = TeamConfig.model_validate(data)
    models = deque(
        [
            ScriptModel(
                output("consult_specialists", requests=[{"role": child, "question": "Review"}]),
                output("request_research", jobs=[{"job_id": "a", "question": "Public facts"}]),
                output("fact_check", message="Manager report"),
                {"text": "Final"},
            ),
            ScriptModel(output("fact_check", message="Peer finding"), {"text": "Finding"}),
            ScriptModel({"text": "Peer check"}),
            ScriptModel(output("fact_check", message="Research finding"), {"text": "Research"}),
            ScriptModel({"text": "Research check"}),
            ScriptModel({"text": "Manager check"}),
        ]
    )
    configs = []

    def factory(config, *_):
        configs.append(config)
        return models.popleft()

    result = await run_team(
        TeamRequest(question="Review", mode="combined"),
        config=cfg,
        deps=TeamDependencies(model_factory=factory),
    )
    assert not models
    assert [(c.model_id, c.reasoning_effort) for c in configs] == [
        ("deepseek-flash", "high"),
        ("deepseek-flash", "high"),
        ("qwen", "xhigh"),
        ("qwen", "medium"),
        ("qwen", "xhigh"),
        ("qwen", "xhigh"),
    ]
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert set(manifest["role_models"]) == {"portfolio_manager", child}
    assert manifest["role_models"][child]["model_id"] == "deepseek-flash"
    assert manifest["model_settings"]["portfolio_manager_reasoning_effort"] == "high"
