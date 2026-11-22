"""Caller private data belongs to the conversation, never reusable role policy."""

import json

import pytest

from tests.support.thin_sdk import ScriptModel
from tests.thin.test_agents import context
from tests.thin.test_portfolio_council import council_config
from portfolio_management.agents.base import build_role
from portfolio_management.prompt_loader import load_prompt


@pytest.mark.parametrize(
    "role",
    ["team_lead", "portfolio_manager", "wheel_expert", "dalio", "buffett", "analyst", "explorer"],
)
async def test_private_inputs_are_once_in_user_history_and_never_system(tmp_path, role):
    class BoundaryModel(ScriptModel):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            self.system = system_prompt
            async for item in super().stream(messages, tool_specs, system_prompt, **kwargs):
                yield item

    model = BoundaryModel({"text": "first"}, {"text": "second"})
    ctx = context(tmp_path, "team_lead", model)
    ctx.config = council_config(tmp_path)
    ctx.prompt = load_prompt(role, ctx.config.prompts)
    ctx.request = {
        "question": "task",
        "mode": "portfolio",
        "portfolio_text": "PRIVATE_PORTFOLIO_MARKER",
        "owner_context": "PRIVATE_OWNER_MARKER",
        "feedback_context": "PRIVATE_FEEDBACK_MARKER",
        "feedback_source_attribution": "PRIVATE_PROVENANCE_MARKER",
    }
    ctx.prior_review = {"prior_report": "PRIVATE_PARENT_MARKER", "parent_run_id": "private-parent"}
    agent = build_role(role, ctx)
    await agent.invoke_async("First assignment")
    await agent.invoke_async("Continue without repeating inputs")
    for marker in [
        "PRIVATE_PORTFOLIO_MARKER",
        "PRIVATE_OWNER_MARKER",
        "PRIVATE_FEEDBACK_MARKER",
        "PRIVATE_PROVENANCE_MARKER",
        "PRIVATE_PARENT_MARKER",
    ]:
        assert marker not in agent.system_prompt
        assert marker not in model.system
        for call in model.calls:
            user_text = "\n".join(
                b.get("text", "")
                for m in call["messages"]
                if m["role"] == "user"
                for b in m["content"]
            )
            inline = role in {"team_lead", "portfolio_manager"} or (
                marker == "PRIVATE_PARENT_MARKER" and role in {"wheel_expert", "dalio", "buffett"}
            )
            assert user_text.count(marker) == int(inline)
    assert (
        json.dumps(ctx.prior_review)
        == '{"prior_report": "PRIVATE_PARENT_MARKER", "parent_run_id": "private-parent"}'
    )


async def test_tool_continuation_refreshes_budget_without_readding_private_data(tmp_path):
    from tests.support.thin_sdk import output

    async def finance_calculate(value: str) -> dict:
        """Return an offline calculation result."""
        return {"value": value}

    model = ScriptModel(output("finance_calculate", value="synthetic"), {"text": "Done"})
    ctx = context(tmp_path, "team_lead", model, {"finance_calculate": finance_calculate})
    ctx.config = council_config(tmp_path)
    ctx.prompt = load_prompt("portfolio_manager", ctx.config.prompts)
    ctx.request = {
        "question": "task",
        "mode": "portfolio",
        "owner_context": "EXACT_PRIVATE_α\r\nsource",
    }
    agent = build_role("portfolio_manager", ctx)
    await agent.invoke_async("Calculate")
    assert len(model.calls) == 2
    for call in model.calls:
        supplied = "".join(
            b.get("text", "") for m in call["messages"] if m["role"] == "user" for b in m["content"]
        )
        assert supplied.count("EXACT_PRIVATE_α\r\nsource") == 1
    assert "EXACT_PRIVATE" not in agent.system_prompt
