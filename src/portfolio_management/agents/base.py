"""Shared SDK assembly driven by policies declared in each role module."""

import json
import time
from typing import Any

from strands import Agent, tool
from strands.agent.conversation_manager import NullConversationManager

from portfolio_management.adapters.portfolio.text import readable_portfolio
from portfolio_management.followup import prompt_review
from portfolio_management.models import RunHooks
from portfolio_management.telemetry.observer import optional_observer
from portfolio_management.types import ResearchResult, RoleContext, TeamRequest

from .continuity import build_continuity as build_continuity
from .definition import RoleDefinition
from .policies import AUTHOR_HANDOFF, COUNCIL_ADVISORY, DIRECT_TEXT_CONTRACT, FACT_CHECK_CONTRACT
from .policies import READ_TOOLS as READ_TOOLS
from .registry import CHECKED_AUTHORS as CHECKED_AUTHORS
from .registry import PRIVATE_EXPERTS as PRIVATE_EXPERTS
from .registry import ROLE_TOOLS as ROLE_TOOLS
from .registry import build_role as build_role
from .registry import role_tools as role_tools
from .team_lead import project_team_result_spec as project_team_result_spec


def dependency_context(market_provider: Any, research_backend: Any) -> dict[str, Any]:
    """Public dependency identity only; no private transport configuration."""
    market_data: dict[str, Any] = {"mode": "unavailable", "capabilities": []}
    if market_provider is not None:
        try:
            description = market_provider.describe() if hasattr(market_provider, "describe") else {}
            market_data = {
                "mode": "unspecified",
                **description,
                "capabilities": market_provider.capabilities(),
            }
        except Exception:
            market_data = {
                "mode": "unavailable",
                "capabilities": [],
                "reason": "Provider description is unavailable",
            }
    research_data: dict[str, Any] = {"mode": "unavailable", "capabilities": []}
    if research_backend is not None:
        try:
            research_data = research_backend.describe()
        except Exception:
            research_data["reason"] = "Research dependency description is unavailable"
    return {"market_provider": market_data, "research_backend": research_data}


def build_agent(definition: RoleDefinition, context: RoleContext) -> Agent:
    role = definition.name
    if context.prompt.role != role:
        raise ValueError("prompt role must match role constructor")
    if context.model is None:
        raise ValueError("A configured or explicitly injected SDK model is required")
    tools: list[Any] = []
    for name, value in context.tools.items():
        if name == "fact_check" and (
            not definition.checked_author or not context.config.fact_check.enabled
        ):
            continue
        if (
            name not in definition.allowed_tools(context.config)
            and not (context.config.fact_check.enabled and name == "fact_check")
            and not (definition.allow_tool_overrides and name in context.tool_override_names)
        ):
            continue
        if hasattr(value, "tool_spec"):
            if value.tool_spec["name"] != name:
                raise ValueError("Supplied tool name must match its permitted binding")
            tools.append(value)
        else:
            tools.append(tool(name=name, description=value.__doc__ or name)(value))
    request = TeamRequest.model_validate(context.request)
    analysis_time = context.trace.clock()
    if analysis_time.utcoffset() is None:
        raise ValueError("Analysis clock must be timezone-aware")
    run_data = {
        "run_id": context.run_dir.name,
        "role": role,
        "analysis_time": analysis_time.isoformat(),
        "mode": request.mode,
        "review_kind": request.review_kind,
        "portfolio_supplied": request.portfolio_text is not None or request.portfolio is not None,
        "invocation_id": context.invocation_id,
        **dependency_context(context.market_provider, context.research_backend),
        "prompt_identity": {
            "prompt_id": context.prompt.prompt_id,
            "version": context.prompt.version,
            "content_hash": context.prompt.content_hash,
            "status": context.prompt.status,
            "owner_approval_ref": context.prompt.owner_approval_ref,
            "shared_hashes": context.prompt.shared_hashes,
            "module_hashes": context.prompt.module_hashes,
        },
    }
    output_type = definition.output_type
    schema = output_type.model_json_schema()
    contract: dict[str, Any] = {
        "tool_name": output_type.__name__,
        "required_fields": schema.get("required", []),
        "required_field_types": {
            name: schema["properties"][name]["type"] for name in schema.get("required", [])
        },
        "max_output_tokens": context.config.model.max_output_tokens,
        "max_output_repairs_after_failed_output_tool": (
            context.config.finalization.max_output_repairs
        ),
        "advisory": (
            "Use one complete JSON object for the output tool, including required fields. "
            "Keep prose concise and cite persisted references instead of repeating raw tables."
        ),
    }
    if output_type is ResearchResult:
        contract["valid_outcomes"] = schema["properties"]["outcome"]["enum"]
    if context.config.child_output_mode == "direct_text" or definition.always_direct_text:
        contract = dict(DIRECT_TEXT_CONTRACT)
    if context.config.council_enabled and definition.council_author:
        if context.config.child_output_mode == "direct_text":
            contract["advisory"] = COUNCIL_ADVISORY
        contract["handoff"] = AUTHOR_HANDOFF
    if definition.configure_contract:
        contract = definition.configure_contract(context, contract)
    if definition.configure_run_data:
        definition.configure_run_data(context, run_data)
    run_data["output_contract"] = contract
    if context.config.fact_check.enabled and definition.checked_author:
        run_data["fact_check_contract"] = FACT_CHECK_CONTRACT
    # Snapshot static dependency descriptions; neither mutable provider metadata nor
    # later suffix refreshes can change the approved prefix or append old contexts.
    static_data = json.loads(json.dumps(run_data))
    prefix = (
        context.prompt.content
        + ("\n\n" + context.fact_check_amendment if context.fact_check_amendment else "")
        + "\n\nRun context (data):\n"
    )

    inline_private = definition.inline_private(context.config)
    portfolio_text = readable_portfolio(request) if inline_private else None
    portfolio_ref = (
        "\n\nSupplied portfolio reference (private data):\n" + request.portfolio.snapshot_id
        if inline_private and request.portfolio is not None
        else ""
    )
    portfolio_suffix = (
        "\n\nOriginal readable portfolio (data):\n" + portfolio_text
        if portfolio_text is not None
        else ""
    )
    owner_suffix = (
        "\n\nOwner context (private supplied data):\n" + request.owner_context
        if inline_private and request.owner_context is not None
        else ""
    )
    prior_suffix = (
        "\n\nPrior review (private ordinary dated data; not current observations):\n"
        + json.dumps(prompt_review(context.prior_review))
        if definition.private_context != "none" and context.prior_review is not None
        else ""
    )

    feedback_suffix = (
        "\n\nSelected prior owner/team and continuity context (private attributed dated data):\n"
        + request.feedback_context
        if inline_private and request.feedback_context is not None
        else ""
    )

    if request.feedback_source_attribution and inline_private:
        feedback_suffix += "\nSelected question provenance: " + request.feedback_source_attribution

    if request.feedback_source_ref is not None:
        feedback_suffix += definition.feedback_guidance

    def system_prompt() -> str:
        now = time.monotonic()
        budget = context.budget
        runtime_budget = {
            "global_seconds_remaining": max(0.0, context.monotonic_deadline - now)
            if context.monotonic_deadline is not None
            else None,
            "invocation_seconds_remaining": max(0.0, context.invocation_deadline - now)
            if context.invocation_deadline is not None
            else None,
            "shared_generations_remaining": max(0, budget.max_generations - budget.generations),
            "invocation_generations_remaining": max(
                0, context.generation_ceiling - budget.generations
            )
            if context.generation_ceiling is not None
            else None,
            (
                "manager_consultations_remaining"
                if context.config.council_enabled
                else "expert_consultations_remaining"
            ): max(0, budget.max_expert_consultations - budget.expert_consultations),
        }
        return prefix + json.dumps({**static_data, "runtime_budget": runtime_budget})

    def refresh_context(agent: Agent) -> None:
        agent.system_prompt = system_prompt()

    private_data = portfolio_ref + portfolio_suffix + owner_suffix + prior_suffix + feedback_suffix
    observer = optional_observer(context, role)
    agent = Agent(
        model=context.model,
        system_prompt=system_prompt(),
        messages=(
            [
                {
                    "role": "user",
                    "content": [{"text": "Caller-supplied private task data:" + private_data}],
                }
            ]
            if private_data
            else []
        ),
        tools=tools,
        hooks=[
            RunHooks(
                context.budget,
                context.trace,
                role,
                context.invocation_id,
                generation_ceiling=context.generation_ceiling,
                max_output_repairs=context.config.finalization.max_output_repairs,
                refresh_context=refresh_context,
                context=context,
            ),
            *([observer] if observer is not None else []),
        ],
        callback_handler=observer,
        retry_strategy=None,
        conversation_manager=NullConversationManager(),
    )

    if definition.install_middleware:
        definition.install_middleware(agent, context)
    return agent
