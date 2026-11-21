"""Small shared constructor; no provider-neutral agent or tool framework."""

import json
import time
from copy import deepcopy
from typing import Any

from pydantic import BaseModel
from strands import Agent, tool
from strands.agent.conversation_manager import NullConversationManager
from strands.middleware import InvokeModelContext, InvokeModelStage
from strands.types.tools import ToolChoiceToolDict, ToolSpec

from wheel_team.adapters.portfolio.text import readable_portfolio
from wheel_team.followup import prompt_review
from wheel_team.models import TEAM_RESULT_CANDIDATE_SLOTS_GUIDANCE, RunHooks
from wheel_team.option_response_view import option_response_messages
from wheel_team.telemetry.observer import optional_observer
from wheel_team.types import ExpertResult, ResearchResult, RoleContext, TeamRequest, TeamResult

PRIVATE_EXPERTS = frozenset({"wheel_expert", "dalio", "buffett", "portfolio_manager"})
CHECKED_AUTHORS = PRIVATE_EXPERTS | {"analyst"}

READ_TOOLS = frozenset({"finance_calculate", "knowledge_read", "evidence_read", "evidence_find"})
ROLE_TOOLS = {
    "team_lead": frozenset({"explore", "research_batch", "consult_expert"}),
    "analyst": READ_TOOLS | {"market_query", "public_search", "fetch_sources"},
    "wheel_expert": READ_TOOLS | {"request_research"},
    "dalio": READ_TOOLS | {"request_research"},
    "buffett": READ_TOOLS | {"request_research"},
    "portfolio_manager": READ_TOOLS | {"request_research", "consult_specialists"},
    "explorer": frozenset({"public_search", "market_statistics", "options_liquidity"}),
}


def role_tools(role: str, config: Any) -> frozenset[str]:
    """Private optional roles do not expand the public delegation/evaluation inventory."""
    if role == "continuity":
        return frozenset(
            {"continuity_list", "continuity_read", "continuity_search", "continuity_write"}
        )
    allowed = ROLE_TOOLS[role]
    if config.council_enabled and role in PRIVATE_EXPERTS:
        allowed = allowed | {"option_scenarios"}
    if role == "portfolio_manager" and config.council_enabled:
        allowed = allowed | {"explore"}
        if config.asymmetric_enabled:
            allowed = allowed | {"consult_asymmetric"}
    if role == "team_lead" and config.council_enabled:
        allowed = (allowed - {"consult_expert"}) | {"consult_portfolio_manager"}
    if role in {"team_lead", "portfolio_manager"} and config.continuity is not None:
        allowed = allowed | {"consult_continuity"}
    if role == "team_lead" and config.asymmetric_enabled:
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


def build_role(role: str, context: RoleContext) -> Agent:
    if context.prompt.role != role:
        raise ValueError("prompt role must match role constructor")
    if context.model is None:
        raise ValueError("A configured or explicitly injected SDK model is required")
    tools: list[Any] = []
    for name, value in context.tools.items():
        if name == "fact_check" and (
            role not in CHECKED_AUTHORS or not context.config.fact_check.enabled
        ):
            continue
        if (
            name not in role_tools(role, context.config)
            and not (context.config.fact_check.enabled and name == "fact_check")
            and not (role == "explorer" and name in context.tool_override_names)
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
    output_types: dict[str, type[BaseModel]] = {
        "team_lead": TeamResult,
        "analyst": ResearchResult,
        "explorer": ResearchResult,
        "wheel_expert": ExpertResult,
        "dalio": ExpertResult,
        "buffett": ExpertResult,
        "portfolio_manager": ExpertResult,
        "continuity": ExpertResult,
    }
    output_type = output_types[role]
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
    if output_type is TeamResult:
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
    if context.config.child_output_mode == "direct_text" or role == "continuity":
        contract = {
            "mode": "direct_text",
            "advisory": (
                "Finish with ordinary assistant final text. This runtime instruction "
                "supersedes older typed output wording, including TeamResult formatting. "
                "No candidate, portfolio or reference metadata is required for delivery. "
                "Preserve exact citations, sources, units and limitations. Any supplied "
                "portfolio and original analyst reports arrive as caller task data; "
                "no publication or reader call is required."
            ),
        }
        if role == "team_lead":
            contract["child_handoff"] = (
                "Call consult_expert() with no arguments for one holistic review. "
                "The host supplies the original owner request, every completed run-local "
                "analyst envelope once, separate explorer originals, and the original "
                "readable portfolio in the expert context. Do not select job IDs, copy "
                "references, or retell financial narratives for this handoff. Partial "
                "child output stays incomplete; empty failures contain no research."
            )
    if context.config.council_enabled and role == "team_lead":
        contract["child_handoff"] = (
            "Call consult_portfolio_manager() with no arguments. The host supplies all "
            "original research and private context. Portfolio Manager original final text "
            "is the authoritative investment report; your final text is coordination only."
        )
    if context.config.council_enabled and role in CHECKED_AUTHORS | {"explorer"}:
        if context.config.child_output_mode == "direct_text":
            contract["advisory"] = (
                "Finish with ordinary authored final text. Preserve material findings, citations "
                "and qualifications. Any supplied private owner/account originals arrive as "
                "caller task data; peers retrieve relevant available originals by reference. "
                "No publication is required."
            )
        contract["handoff"] = (
            "You are the author: finish with a concise useful final answer to the assignment, "
            "material findings, uncertainty and selected citations. Omit research history, "
            "source dumps, evidence inventories or hidden reasoning. Your answer is preserved "
            "exactly, never silently shortened. Child tools return final-result views and stable "
            "input refs. Use evidence_read for a bounded view of a selected envelope or source; "
            "large views are labeled incomplete excerpts. Select "
            "relevant refs for fact_check. Completion describes execution, not correctness."
        )
        if role == "portfolio_manager":
            contract["planning"] = (
                "Start by planning the original owner task. For full reviews, public research, "
                "explore, optional asymmetric discovery and all three broad peers are available "
                "under your direction. Request independent Wheel, Dalio and Buffett views where "
                "useful, including together in one consult_specialists call. Supply only relevant "
                "evidence_refs to peers; they can selectively read/check originals. You retain "
                "final authority and may challenge earlier DCA assumptions."
            )
    if role == "continuity":
        contract = {
            "mode": "direct_text",
            "advisory": (
                "Finish with ordinary text. File access and save receipts are recorded by the host."
            ),
        }
    if context.config.council_enabled and role in {"wheel_expert", "dalio", "buffett"}:
        run_data["private_context"] = (
            "Your assignment is supplied separately. Bounded views of private originals are "
            "available on demand through evidence_read(record_ref=...) and fact_check refs: "
            "input:portfolio, input:owner_context, supplied input:continuity:N, "
            "input:analyst:N, input:explorer:N, input:asymmetric:N and your own "
            "input:specialist:N. Request only relevant originals; retain dates and uncertainty. "
            "No account facts are inferred from missing inline context."
        )
    run_data["output_contract"] = contract
    if context.config.fact_check.enabled and role in CHECKED_AUTHORS:
        run_data["fact_check_contract"] = (
            "Before returning factual claims, use fact_check(message, evidence_refs=[], "
            "assumptions=[]) to obtain reasoning feedback. Select only relevant original refs; "
            "nothing else is attached. Repair material errors; recheck materially changed "
            "claims when useful. "
            "At most three calls. Completed means execution, not factual approval. "
            "Preserve conditionality and disclose missing evidence. The checker cannot fetch "
            "sources. Expert may explicitly select input:portfolio, input:owner_context, "
            "or supplied input:continuity:N (the exact attributed private brief, "
            "not underlying notes). Private authors may select input:analyst:N, "
            "input:explorer:N, input:asymmetric:N and input:specialist:N for the "
            "numbered original report envelopes supplied in this invocation. "
            "Specialist originals retain author identity and outcome; they are judgments."
        )
    # Snapshot static dependency descriptions; neither mutable provider metadata nor
    # later suffix refreshes can change the approved prefix or append old contexts.
    static_data = json.loads(json.dumps(run_data))
    prefix = (
        context.prompt.content
        + ("\n\n" + context.fact_check_amendment if context.fact_check_amendment else "")
        + "\n\nRun context (data):\n"
    )

    inline_private = role in PRIVATE_EXPERTS | {"team_lead"} and not (
        context.config.council_enabled and role in {"wheel_expert", "dalio", "buffett"}
    )
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
        if role in PRIVATE_EXPERTS | {"team_lead"} and context.prior_review is not None
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

    if request.feedback_source_ref is not None and role == "team_lead":
        feedback_suffix += (
            "\n\nThis is selected owner feedback with separately attributed original and "
            "derived input. Investment questions require "
            "the Expert assessment; a recording-only statement can omit discussion. "
            "Continuity may supply relevant prior context. The host performs final memory "
            "maintenance with separately attributed originals and actual write receipts. "
            "No execution or owner decision is implied by an agent proposal.\n"
        )

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

    def terminal_tools(model_context: InvokeModelContext) -> InvokeModelContext:
        if context.terminal_only:
            choice: ToolChoiceToolDict = {"tool": {"name": "ResearchResult"}}
            return model_context.replace(
                tool_specs=[
                    spec for spec in model_context.tool_specs if spec["name"] == "ResearchResult"
                ],
                tool_choice=choice,
            )
        return model_context

    if role in {"analyst", "explorer"}:

        def option_views(model_context: InvokeModelContext) -> InvokeModelContext:
            return model_context.replace(messages=option_response_messages(model_context.messages))

        agent.add_middleware(InvokeModelStage.Input, option_views)
        agent.add_middleware(InvokeModelStage.Input, terminal_tools)
    if role == "team_lead":

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
                    role=role,
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
                if context.config.child_output_mode == "direct_text" or role == "continuity":
                    return model_context.replace(tool_specs=[], tool_choice=None)
                choice: ToolChoiceToolDict = {"tool": {"name": "TeamResult"}}
                return model_context.replace(tool_specs=specs, tool_choice=choice)
            return model_context.replace(tool_specs=specs)

        agent.add_middleware(InvokeModelStage.Input, lead_output)
    return agent


def build_continuity(context: RoleContext) -> Agent:
    """Build the private direct-text memory role with only host-scoped file tools."""
    return build_role("continuity", context)
