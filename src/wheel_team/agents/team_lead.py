"""Lead coordination permissions, handoff contract and synthesis reserve middleware."""

import time
from copy import deepcopy
from typing import Any

from strands import Agent
from strands.middleware import InvokeModelContext, InvokeModelStage
from strands.types.tools import ToolChoiceToolDict, ToolSpec

from wheel_team.config import TeamConfig
from wheel_team.models import TEAM_RESULT_CANDIDATE_SLOTS_GUIDANCE
from wheel_team.types import RoleContext, TeamResult

from .definition import RoleDefinition

BASE_TOOLS = frozenset({"explore", "research_batch", "consult_expert"})


def allowed_tools(config: TeamConfig, base_tools: frozenset[str]) -> frozenset[str]:
    allowed = base_tools
    if config.council_enabled:
        allowed = (allowed - {"consult_expert"}) | {"consult_portfolio_manager"}
    if config.continuity is not None:
        allowed = allowed | {"consult_continuity"}
    if config.asymmetric_enabled:
        allowed = allowed | {"consult_asymmetric"}
    return allowed


def project_team_result_spec(spec: ToolSpec) -> ToolSpec:
    """Copy the lead's advertised contract, leaving native validation untouched."""
    projected = deepcopy(spec)
    schema = projected["inputSchema"]["json"]
    schema["properties"].pop("report_markdown", None)
    if "required" in schema:
        schema["required"] = [name for name in schema["required"] if name != "report_markdown"]
    slots = schema["properties"]["candidate_slots"]
    slots["type"] = "array"
    slots["description"] = TEAM_RESULT_CANDIDATE_SLOTS_GUIDANCE
    return projected


def output_contract(context: RoleContext, contract: dict[str, Any]) -> dict[str, Any]:
    if context.config.child_output_mode != "direct_text":
        contract["field_guidance"] = {"candidate_slots": TEAM_RESULT_CANDIDATE_SLOTS_GUIDANCE}
        contract["omit_host_populated_fields"] = [
            "run_id",
            "mode",
            "report_markdown",
            "supporting_records",
            "portfolio_snapshot",
            "started_at",
            "completed_at",
            "model_identity",
            "prompt_identities",
            "owner_policy",
            "artifacts",
            "lineage",
        ]
    else:
        contract["child_handoff"] = (
            "Call consult_expert() with no arguments for one holistic review. The "
            "host supplies the original owner request, every completed run-local "
            "analyst envelope once, separate explorer originals, and the original "
            "readable portfolio in the expert context. Do not select job IDs, copy "
            "references, or retell financial narratives for this handoff. Partial "
            "child output stays incomplete; empty failures contain no research."
        )
    if context.config.council_enabled:
        contract["child_handoff"] = (
            "Call consult_portfolio_manager() with no arguments. The host supplies "
            "all original research and private context. Portfolio Manager original "
            "final text is the authoritative investment report; your final text is "
            "coordination only."
        )
    return contract


def install_lead_middleware(agent: Agent, context: RoleContext) -> None:
    def lead_output(model_context: InvokeModelContext) -> InvokeModelContext:
        policy = context.config.finalization
        time_reserve = (
            policy.expert_reserve_seconds > 0
            and context.monotonic_deadline is not None
            and context.monotonic_deadline - time.monotonic() <= policy.expert_reserve_seconds
        )
        # The before-model hook has already charged this generation.
        generation_reserve = (
            policy.expert_reserve_generations > 0
            and context.budget.generations
            > context.budget.max_generations - policy.expert_reserve_generations
        )
        if not context.terminal_only and (time_reserve or generation_reserve):
            context.terminal_only = True
            context.trace.emit(
                "lead_synthesis_started",
                role="team_lead",
                invocation_id=context.invocation_id,
                generation=context.budget.generations,
                boundary="time_reserve" if time_reserve else "generation_reserve",
            )
        specs = [
            project_team_result_spec(spec) if spec["name"] == "TeamResult" else spec
            for spec in model_context.tool_specs
            if not context.terminal_only or spec["name"] == "TeamResult"
        ]
        if context.terminal_only:
            if context.config.child_output_mode == "direct_text":
                return model_context.replace(tool_specs=[], tool_choice=None)
            choice: ToolChoiceToolDict = {"tool": {"name": "TeamResult"}}
            return model_context.replace(tool_specs=specs, tool_choice=choice)
        return model_context.replace(tool_specs=specs)

    agent.add_middleware(InvokeModelStage.Input, lead_output)


DEFINITION = RoleDefinition(
    name="team_lead",
    base_tools=BASE_TOOLS,
    output_type=TeamResult,
    tools_for_config=allowed_tools,
    private_context="inline",
    configure_contract=output_contract,
    install_middleware=install_lead_middleware,
    feedback_guidance=(
        "\n\nThis is selected owner feedback with separately attributed original "
        "and derived input. Investment questions require the Expert assessment; "
        "a recording-only statement can omit discussion. Continuity may supply "
        "relevant prior context. The host performs final memory maintenance with "
        "separately attributed originals and actual write receipts. No execution "
        "or owner decision is implied by an agent proposal.\n"
    ),
)


def build_team_lead(context: RoleContext) -> Agent:
    from .base import build_agent

    return build_agent(DEFINITION, context)
