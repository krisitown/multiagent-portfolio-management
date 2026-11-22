"""Offline contracts for the three-hour explorer research budget."""

import tomllib
from pathlib import Path

import pytest

from portfolio_management.config import ModelConfig, TeamConfig


def test_unconfigured_legacy_defaults_match_ae0dde0():
    config = TeamConfig(model=ModelConfig(model_id="offline"))
    assert config.deadline_seconds == 2700
    assert config.model.timeout_seconds == 60
    assert config.model.max_output_tokens == 4096
    assert config.analyst_concurrency == 4
    assert config.max_expert_consultations == 5
    assert config.max_generations == 100
    assert config.explorer_enabled is False
    assert config.finalization.analyst_reserve_seconds == 0
    assert config.finalization.expert_reserve_seconds == 0
    assert config.finalization.analyst_reserve_generations == 0
    assert config.finalization.expert_reserve_generations == 0


def test_explicit_legacy_limits_remain_recoverable():
    config = TeamConfig(
        model=ModelConfig(model_id="offline", timeout_seconds=60, max_output_tokens=4096),
        analyst_concurrency=4,
        max_expert_consultations=5,
        max_generations=100,
        deadline_seconds=2700,
    )
    assert config.explorer_enabled is False
    assert config.max_expert_consultations == 5
    assert config.model.timeout_seconds == 60
    assert config.deadline_seconds == 2700


@pytest.mark.parametrize("name", ["local-example.toml", "scripted.toml"])
def test_new_example_profiles_explicitly_enable_explorer(name):
    data = tomllib.loads((Path(__file__).parents[2] / "evals/configs" / name).read_text())
    config = TeamConfig.model_validate(data)
    assert config.explorer_enabled is True
    assert config.deadline_seconds == 10800
    assert config.max_generations == 1000
    assert config.analyst_concurrency == 5
    assert config.max_expert_consultations == 1
    assert config.model.timeout_seconds >= 10800
    assert config.model.max_output_tokens == 65536
    assert config.prompts.mode == "development"


def test_actual_shared_budget_stops_at_three_hours_or_generation_cap(tmp_path):
    import asyncio
    from datetime import UTC, datetime, timedelta

    from portfolio_management.models import GenerationLimitExceeded, RunDeadlineExceeded, check_budget
    from portfolio_management.types import RunBudget, TraceSink

    data = tomllib.loads((Path(__file__).parents[2] / "evals/configs/scripted.toml").read_text())
    config = TeamConfig.model_validate(data)
    start = datetime(2026, 10, 31, tzinfo=UTC)
    now = start + timedelta(seconds=10799)
    budget = RunBudget(
        config.max_generations,
        config.max_expert_consultations,
        start + timedelta(seconds=config.deadline_seconds),
        asyncio.Semaphore(config.analyst_concurrency),
    )
    trace = TraceSink(tmp_path / "trace.jsonl", lambda: now)
    budget.generations = 999
    check_budget(budget, trace)
    budget.generations = 1000
    with pytest.raises(GenerationLimitExceeded):
        check_budget(budget, trace)
    budget.generations = 999
    now = start + timedelta(seconds=10800)
    with pytest.raises(RunDeadlineExceeded):
        check_budget(budget, trace)


def test_explorer_hooks_use_research_output_tool(tmp_path):
    import asyncio
    from datetime import UTC, datetime, timedelta

    from portfolio_management.models import RunHooks
    from portfolio_management.types import RunBudget, TraceSink

    now = datetime(2026, 10, 31, tzinfo=UTC)
    budget = RunBudget(1000, 1, now + timedelta(seconds=10800), asyncio.Semaphore(5))
    hooks = RunHooks(
        budget, TraceSink(tmp_path / "trace.jsonl", lambda: now), "explorer", "explorer-1"
    )
    assert hooks.output_tool == "ResearchResult"


@pytest.mark.parametrize(
    "reserve",
    [
        "analyst_reserve_seconds",
        "expert_reserve_seconds",
        "analyst_reserve_generations",
        "expert_reserve_generations",
    ],
)
def test_explorer_rejects_short_role_phase_reserves(reserve):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="without role phase reserves"):
        TeamConfig(
            model=ModelConfig(model_id="offline"),
            explorer_enabled=True,
            max_expert_consultations=1,
            finalization={reserve: 1},
        )


@pytest.mark.parametrize("consultations", [2, 5])
def test_enabled_explorer_rejects_repeated_expert_consultations(consultations):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="at most one expert consultation"):
        TeamConfig(
            model=ModelConfig(model_id="offline"),
            explorer_enabled=True,
            max_expert_consultations=consultations,
        )


@pytest.mark.parametrize("consultations", [0, 1])
def test_enabled_explorer_accepts_explicit_short_offline_limits(consultations):
    config = TeamConfig(
        model=ModelConfig(model_id="offline", timeout_seconds=1),
        explorer_enabled=True,
        max_expert_consultations=consultations,
        deadline_seconds=0.1,
        max_generations=3,
        analyst_concurrency=1,
    )
    assert config.deadline_seconds == 0.1
    assert config.max_expert_consultations == consultations
