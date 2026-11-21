"""Pin constructed SDK inputs across the role/configuration matrix during refactors."""

import asyncio
import hashlib
import itertools
import json
from datetime import timedelta
from pathlib import Path

import pytest

from tests.support.thin_fakes import fixed_clock
from tests.thin.test_entrypoint import config, snapshot
from wheel_team.agents import base
from wheel_team.config import ContinuityConfig, FactCheckConfig
from wheel_team.contracts import PromptBundle
from wheel_team.types import ResearchResult, RoleContext, RunBudget, TeamRequest, TraceSink

ROLES = (
    "team_lead",
    "analyst",
    "wheel_expert",
    "dalio",
    "buffett",
    "portfolio_manager",
    "explorer",
    "continuity",
)
TOOL_NAMES = {
    "finance_calculate",
    "knowledge_read",
    "evidence_read",
    "evidence_find",
    "explore",
    "research_batch",
    "consult_expert",
    "market_query",
    "public_search",
    "fetch_sources",
    "request_research",
    "consult_specialists",
    "market_statistics",
    "options_liquidity",
    "option_scenarios",
    "consult_asymmetric",
    "consult_portfolio_manager",
    "consult_continuity",
    "continuity_list",
    "continuity_read",
    "continuity_search",
    "continuity_write",
    "fact_check",
    "extra_research",
    "forbidden_mutation",
}
BASELINE = Path(__file__).with_name("agent_construction_baseline.json")


class CapturedAgent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.middleware = []

    def add_middleware(self, stage, callback):
        self.middleware.append((type(stage).__name__, callback.__name__))


def make_context(tmp_path, role, flags):
    council, asymmetric, continuity, checked, direct = flags
    cfg = config(tmp_path).model_copy(
        update={
            "council_enabled": council,
            "asymmetric_enabled": asymmetric,
            "continuity": ContinuityConfig(root=Path("/synthetic/memory")) if continuity else None,
            "fact_check": FactCheckConfig(enabled=checked),
            "child_output_mode": "direct_text" if direct else "typed",
        }
    )

    async def visible(value: str = "") -> dict:
        """Read synthetic evidence."""
        return {"value": value}

    return RoleContext(
        request=TeamRequest(
            question="Review synthetic positions",
            mode="portfolio",
            portfolio=snapshot(),
            portfolio_text="Fictional portfolio: Example A, 10 shares.",
            owner_context="Fictional owner preferences.",
            feedback_context="Attributed synthetic prior context.",
            feedback_source_ref="feedback:synthetic",
            feedback_source_attribution="Synthetic owner question.",
        ),
        config=cfg,
        model=object(),
        prompt=PromptBundle.model_construct(
            role=role,
            prompt_id="synthetic-role",
            version="v1",
            content="Synthetic prompt.",
            content_hash="synthetic-hash",
            status="development",
        ),
        tools=dict.fromkeys(sorted(TOOL_NAMES), visible),
        budget=RunBudget(100, 5, fixed_clock() + timedelta(minutes=45), asyncio.Semaphore(4)),
        trace=TraceSink(tmp_path / "trace.jsonl", fixed_clock),
        run_dir=Path("/synthetic/run"),
        invocation_id="synthetic-invocation",
        monotonic_deadline=100,
        invocation_deadline=80,
        generation_ceiling=50,
        tool_override_names=frozenset({"extra_research"}),
        prior_review={"report": "Synthetic prior report", "artifact_hashes": {"a": "hash"}},
        fact_check_amendment="Synthetic checking amendment.",
    )


def construction_signatures(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "Agent", CapturedAgent)
    monkeypatch.setattr(base.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(base, "optional_observer", lambda *_: None)
    signatures = {}
    for role, flags in itertools.product(ROLES, itertools.product([False, True], repeat=5)):
        ctx = make_context(tmp_path, role, flags)
        agent = base.build_role(role, ctx)
        kwargs = agent.kwargs
        signature = {
            "system_prompt": kwargs["system_prompt"],
            "messages": kwargs["messages"],
            "tools": [value.tool_spec for value in kwargs["tools"]],
            "hooks": [type(hook).__name__ for hook in kwargs["hooks"]],
            "middleware": agent.middleware,
            "conversation_manager": type(kwargs["conversation_manager"]).__name__,
            "callback_handler": kwargs["callback_handler"],
            "retry_strategy": kwargs["retry_strategy"],
        }
        key = role + ":" + "".join(str(int(flag)) for flag in flags)
        signatures[key] = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    return signatures


def test_constructed_agents_match_pre_refactor_inputs(tmp_path, monkeypatch):
    assert construction_signatures(tmp_path, monkeypatch) == json.loads(BASELINE.read_text())


def test_shared_builder_uses_supplied_definition_permissions(tmp_path, monkeypatch):
    from wheel_team.agents.definition import RoleDefinition

    monkeypatch.setattr(base, "Agent", CapturedAgent)
    ctx = make_context(tmp_path, "analyst", (False,) * 5)
    definition = RoleDefinition(
        name="analyst",
        base_tools=frozenset({"continuity_read"}),
        output_type=ResearchResult,
    )
    agent = base.build_agent(definition, ctx)
    assert [value.tool_spec["name"] for value in agent.kwargs["tools"]] == ["continuity_read"]
    data = json.loads(agent.kwargs["system_prompt"].split("Run context (data):\n")[1])
    assert data["role"] == "analyst"
    assert data["output_contract"]["tool_name"] == "ResearchResult"
    assert agent.kwargs["messages"] == []


def test_configured_permissions_extend_the_role_modules_declared_tools(tmp_path):
    from dataclasses import replace

    from wheel_team.agents.registry import DEFINITIONS

    cfg = config(tmp_path).model_copy(update={"council_enabled": True})
    for name in ["team_lead", "portfolio_manager", "wheel_expert", "dalio", "buffett"]:
        definition = replace(DEFINITIONS[name], base_tools=frozenset({"extra_research"}))
        assert "extra_research" in definition.allowed_tools(cfg), name


def test_legacy_read_tool_inventory_remains_importable():
    from wheel_team.agents.base import READ_TOOLS

    assert READ_TOOLS == {"finance_calculate", "knowledge_read", "evidence_read", "evidence_find"}


def test_compatibility_dispatch_preserves_validation_precedence(tmp_path):
    ctx = make_context(tmp_path, "analyst", (False,) * 5)
    with pytest.raises(ValueError, match="prompt role"):
        base.build_role("unknown", ctx)
    ctx.prompt = ctx.prompt.model_copy(update={"role": "unknown"})
    ctx.model = None
    with pytest.raises(ValueError, match="configured or explicitly injected"):
        base.build_role("unknown", ctx)
