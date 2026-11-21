"""Offline explorer wiring; these fixtures do not approve research assets."""

import pytest

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_agents import context
from wheel_team.config import PromptSet
from wheel_team.prompt_loader import PromptReadinessError, load_prompt
from wheel_team.types import ResearchResult


def test_explorer_prompt_is_explicitly_unapproved():
    from wheel_team.contracts import AgentRole

    prompt = load_prompt(AgentRole.EXPLORER, PromptSet(mode="development"))
    assert prompt.status == "development"
    assert prompt.owner_approval_ref is None
    assert "DEVELOPMENT ONLY" in prompt.content
    assert "owner workshop" in prompt.content.lower()
    with pytest.raises(PromptReadinessError):
        load_prompt(AgentRole.EXPLORER, PromptSet(mode="production"))


async def test_explorer_uses_configured_model_research_envelope_and_injected_tools(tmp_path):
    from wheel_team.agents import build_explorer

    async def visible(value: str = "") -> dict:
        """Synthetic read-only probe."""
        return {"value": value}

    model = ScriptModel(
        output("ResearchResult", job_id="explore-1", outcome="success", summary="Synthetic context")
    )
    ctx = context(
        tmp_path,
        "explorer",
        model,
        {
            "public_search": visible,
            "market_query": visible,
            "market_statistics": visible,
            "options_liquidity": visible,
            "fetch_sources": visible,
            "workshop_probe": visible,
            "research_batch": visible,
            "consult_expert": visible,
        },
    )
    ctx.tool_override_names = frozenset({"workshop_probe"})
    agent = build_explorer(ctx)
    result = await agent.invoke_async(
        "Synthetic exploration", structured_output_model=ResearchResult
    )
    assert result.structured_output.job_id == "explore-1"
    names = {spec["name"] for spec in model.calls[0]["tools"]}
    assert {
        "public_search",
        "market_statistics",
        "options_liquidity",
        "workshop_probe",
        "ResearchResult",
    } <= names
    assert names.isdisjoint({"research_batch", "consult_expert", "market_query", "fetch_sources"})
    assert '"tool_name": "ResearchResult"' in agent.system_prompt
    assert "Synthetic context" in result.structured_output.summary


def test_explorer_rejects_wrong_prompt_binding(tmp_path):
    from wheel_team.agents import build_explorer

    with pytest.raises(ValueError, match="prompt role"):
        build_explorer(context(tmp_path, "analyst", ScriptModel()))
