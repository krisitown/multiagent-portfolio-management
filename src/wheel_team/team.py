"""One foreground Strands lead with bounded awaited analyst/expert delegation."""

import asyncio
import hashlib
import json
import os
import re
from collections import deque
from collections.abc import Coroutine, Sequence
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel
from strands import Agent, tool
from strands.hooks.events import AfterModelCallEvent
from strands.tools.structured_output.structured_output_tool import StructuredOutputTool
from strands.types.exceptions import MaxTokensReachedException

from wheel_team.adapters.portfolio.text import readable_portfolio
from wheel_team.agents import (
    build_analyst,
    build_buffett,
    build_dalio,
    build_explorer,
    build_portfolio_manager,
    build_team_lead,
    build_wheel_expert,
)
from wheel_team.agents.base import (
    CHECKED_AUTHORS,
    PRIVATE_EXPERTS,
    ROLE_TOOLS,
    build_continuity,
    dependency_context,
    project_team_result_spec,
    role_tools,
)
from wheel_team.checks import render_result
from wheel_team.config import TeamConfig
from wheel_team.continuity import ContinuityError, ContinuityFiles, ContinuityTool, continuity_tools
from wheel_team.fact_check import TokenCounter
from wheel_team.fact_check_runtime import ReasoningCheckSession
from wheel_team.knowledge_loader import load_knowledge
from wheel_team.models import (
    InvocationLimitExceeded,
    budget_failure,
    build_model,
    check_budget,
    invocation_failure,
)
from wheel_team.prompt_loader import load_caller_amendments, load_prompt
from wheel_team.tools import (
    evidence_tools,
    finance_tools,
    knowledge_tools,
    market_tools,
    portfolio_tools,
)
from wheel_team.tools.evidence import bounded_record_view
from wheel_team.tools.explorer import explorer_tools
from wheel_team.tools.option_scenarios import option_scenario_tools
from wheel_team.tools.research import research_tools
from wheel_team.types import (
    ExpertResult,
    ResearchJob,
    ResearchResult,
    RoleContext,
    RoleName,
    RunBudget,
    SpecialistRequest,
    TeamDependencies,
    TeamRequest,
    TeamResult,
    TraceSink,
)
from wheel_team.vault import EvidenceStore


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


async def _invoke(
    agent: Any, question: str, output: type[BaseModel], *, terminal: bool = False
) -> Any:
    # Keep the Task1 injected role seam; SDK formatting always stays inside invoke_async.
    response = (
        await agent.invoke_async(
            question, structured_output_model=output, limits={"turns": 1} if terminal else None
        )
        if isinstance(agent, Agent)
        else await agent.invoke_async(question)
    )
    if (
        terminal
        and getattr(response, "structured_output", None) is None
        and getattr(response, "stop_reason", None) == "limit_turns"
    ):
        raise InvocationLimitExceeded("generation_reserve")
    return getattr(response, "structured_output", response)


async def _invoke_child_text(agent: Any, question: str) -> tuple[str, bool]:
    """Read only the actual completed message, or SDK-confirmed token-cutoff message."""
    cutoff_text: str | None = None

    def observe_cutoff(event: AfterModelCallEvent) -> None:
        nonlocal cutoff_text
        stop = event.stop_response
        if event.exception is None and stop is not None and stop.stop_reason == "max_tokens":
            # Observe before Strands replaces truncated tool calls with synthetic text.
            message = stop.message
            if message.get("role") == "assistant":
                cutoff_text = "".join(
                    block["text"]
                    for block in message.get("content", [])
                    if isinstance(block.get("text"), str)
                )

    if isinstance(agent, Agent):
        agent.hooks.add_callback(AfterModelCallEvent, observe_cutoff)
    try:
        response = await agent.invoke_async(question)
    except MaxTokensReachedException:
        if cutoff_text is None or not cutoff_text.strip():
            raise
        return cutoff_text, True
    else:
        reason = getattr(response, "stop_reason", None)
        if reason not in {"end_turn", "max_tokens"}:
            raise ValueError("Child did not complete ordinary text")
        message, partial = response.message, reason == "max_tokens"
    content = message.get("content", [])
    if message.get("role") != "assistant" or any("toolUse" in block for block in content):
        raise ValueError("Child has no ordinary assistant narrative")
    text = "".join(block["text"] for block in content if isinstance(block.get("text"), str))
    if not text.strip():
        raise ValueError("Child has no ordinary assistant narrative")
    return text, partial


def preserve_partial_research(
    result: TeamResult,
    research_results: Sequence[ResearchResult],
    expert_results: Sequence[ExpertResult] = (),
) -> TeamResult:
    """Keep explicit completed child outputs in a stopped run, without final synthesis."""
    if result.completion_reason == "complete":
        return result
    summaries, assessments = [], []
    refs = list(result.supporting_refs)
    for child in research_results:
        if child.outcome not in {"success", "partial"}:
            continue
        refs.extend(child.evidence_refs + child.idea_refs)
        if child.summary.strip():
            summaries.append(f"### Analyst {child.job_id} ({child.outcome})\n\n{child.summary}")
    for expert in expert_results:
        if expert.error is not None:
            continue
        refs.extend(expert.shortlist_refs)
        if expert.assessment.strip():
            assessments.append(expert.assessment)
    sections = [result.summary]
    if summaries:
        sections.append(
            "## Partial analyst research\n\n"
            "Saved analyst outputs; a completed team investment judgment is not implied.\n\n"
            + "\n\n".join(summaries)
        )
    if assessments:
        sections.append(
            "## Completed expert assessments (partial run)\n\n" + "\n\n".join(assessments)
        )
    return result.model_copy(
        update={"summary": "\n\n".join(sections), "supporting_refs": tuple(dict.fromkeys(refs))}
    )


async def run_team(
    request: TeamRequest, *, config: TeamConfig, deps: TeamDependencies
) -> TeamResult:
    """Run once; cancellation returns a saved partial result after awaiting child cleanup."""
    stop_at = asyncio.get_running_loop().time() + config.deadline_seconds
    request = TeamRequest.model_validate(request)
    if request.feedback_source_ref is not None and config.continuity is None:
        raise ValueError("Feedback maintenance requires configured private continuity")
    from wheel_team.followup import (
        checkpoint_files,
        inherit_private_inputs,
        load_parent_review,
        prompt_review,
    )

    feedback_report_only = request.feedback_source_ref is not None
    parent = (
        load_parent_review(Path(request.parent_review), report_only=feedback_report_only)
        if request.parent_review
        else None
    )
    if parent is not None and not feedback_report_only:
        request = inherit_private_inputs(request, parent["request"])
    started = deps.clock()
    if started.utcoffset() is None:
        raise ValueError("Run clock must be timezone-aware")
    # Freeze every selected role/shared module before any model or asynchronous work.
    roles = [
        role
        for role in ROLE_TOOLS
        if role != "continuity"
        and (role != "explorer" or config.explorer_enabled)
        and (config.council_enabled or role not in {"dalio", "buffett", "portfolio_manager"})
    ]
    prompts = {role: load_prompt(role, config.prompts) for role in roles}
    if config.fact_check.enabled:
        prompts["fact_checker"] = load_prompt("fact_checker", config.fact_check.prompts)
    if config.continuity is not None:
        prompts["continuity"] = load_prompt("continuity", config.continuity.prompts)
    amendments, amendment_hashes = load_caller_amendments(
        config.fact_check.caller_amendments_path if config.fact_check.enabled else None, prompts
    )
    token_counter = TokenCounter.local(
        config.fact_check.tokenizer_path if config.fact_check.enabled else None
    )
    if config.model.provider == "scripted" and deps.evaluation_mode != "scripted":
        raise ValueError("Scripted inference cannot be labelled role/team evaluation")
    role_scripts: dict[str, Any] | None = None
    if config.model.provider == "scripted" and isinstance(config.model.script, dict):
        scripts = config.model.script.get("role_scripts")
        if scripts is not None:
            if (
                not isinstance(scripts, dict)
                or set(scripts) - set(prompts)
                or any(not isinstance(invocations, list) for invocations in scripts.values())
            ):
                raise ValueError("Development role_scripts must select only configured team roles")
            role_scripts = {
                role: deque(invocations)
                for role, invocations in cast(dict[str, list[Any]], scripts).items()
            }
    knowledge = (
        load_knowledge(config.knowledge_manifest_path) if config.knowledge_manifest_path else None
    )
    if (
        knowledge is not None
        and knowledge.status != "approved"
        and config.prompts.mode != "development"
    ):
        raise ValueError("Development knowledge requires explicit development selection")
    # Reject disabled live inference before artifacts, even when there is no configured key.
    if (
        deps.model_factory is None
        and deps.role_factory is None
        and config.model.provider == "openai"
    ):
        if not config.model.allow_inference_network:
            raise ValueError("Inference network requires explicit allow_inference_network=true")
    run_id = "run-" + uuid4().hex
    run_dir = config.output_dir.expanduser().resolve() / run_id
    run_dir.mkdir(parents=True, mode=0o700)
    paths = {
        name: str(run_dir / filename)
        for name, filename in {
            "request": "request.json",
            "manifest": "manifest.json",
            "trace": "trace.jsonl",
            "result": "result.json",
            "report": "report.md",
            "research": "research-results.json",
            "expert": "expert-results.json",
            "expert_research": "expert-research-requests.json",
            "explorer": "explorer-results.json",
            "review_checkpoint": "review-checkpoint.json",
        }.items()
    }
    budget = RunBudget(
        max_generations=config.max_generations,
        max_expert_consultations=config.max_expert_consultations,
        deadline=started + timedelta(seconds=config.deadline_seconds),
        analyst_semaphore=asyncio.Semaphore(config.analyst_concurrency),
    )
    trace = TraceSink(
        Path(paths["trace"]), deps.clock, best_effort_io=config.child_output_mode == "direct_text"
    )
    from wheel_team.privacy import PrivateEvidenceStore

    public_store = EvidenceStore(run_dir / "records")
    store = PrivateEvidenceStore(run_dir / "private-records", public_store)
    if parent is not None:
        for scope, target in [("public_records", public_store), ("private_records", store)]:
            for envelope in parent[scope]:
                if target.put(envelope["kind"], envelope["record"]) != envelope["ref"]:
                    raise ValueError("Parent record identity changed during scoped import")
        paths["parent_review"] = str(run_dir / "parent-review.json")
        _write_json(Path(paths["parent_review"]), parent["packet"])
    lineage = {
        "review_kind": request.review_kind,
        "parent_run_id": parent["packet"]["parent_run_id"] if parent else None,
        "review_session_id": parent["packet"]["review_session_id"] if parent else run_id,
    }
    # Factories bind concrete run dependencies once. No tool registry or services are imported.
    available = {
        **evidence_tools(store),
        **portfolio_tools(store, request.portfolio),
        **finance_tools(store, request.portfolio, config.policy, deps.clock),
        **knowledge_tools(public_store, config.knowledge_manifest_path),
        **market_tools(public_store, deps.market_provider),
        **research_tools(public_store, deps.research_backend, deps.clock),
    }
    if config.council_enabled:
        available.update(option_scenario_tools(store, deps.clock))
    public_available = {
        **available,
        **evidence_tools(public_store),
        **finance_tools(public_store, clock=deps.clock),
    }
    explorer_available = (
        explorer_tools(public_store, deps.market_provider, deps.research_backend, deps.clock)
        if config.explorer_enabled
        else {}
    )
    _write_json(Path(paths["request"]), request.model_dump(mode="json"))
    lead_prompt = prompts["team_lead"]
    manifest: dict[str, Any] = {
        "record_stores": {"private": str(store.root), "public": str(public_store.root)},
        "lineage": lineage,
        **dependency_context(deps.market_provider, deps.research_backend),
        "run_id": run_id,
        "started_at": started.isoformat(),
        "mode": request.mode,
        "child_output_mode": config.child_output_mode,
        "evaluation_mode": deps.evaluation_mode,
        "live_acceptance": False,
        "feedback_record_only": request.feedback_record_only,
        "provider": config.model.provider,
        "model_id": config.model.model_id,
        "role_models": {
            role: model.model_dump(mode="json", exclude={"script"})
            for role, model in config.role_models.items()
        },
        "portfolio_manager_model": (
            config.portfolio_manager_model.model_dump(mode="json", exclude={"script"})
            if config.portfolio_manager_model is not None
            else None
        ),
        "fixture_sha256": hashlib.sha256(
            json.dumps(config.model.script, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if config.model.script is not None
        else None,
        "model_settings": {
            "endpoint": config.model.endpoint,
            "timeout_seconds": config.model.timeout_seconds,
            "max_output_tokens": config.model.max_output_tokens,
            "reasoning_effort": config.model.reasoning_effort,
            "portfolio_manager_reasoning_effort": config.portfolio_manager_reasoning_effort
            or (
                config.role_models.get("portfolio_manager")
                or config.portfolio_manager_model
                or config.model
            ).reasoning_effort,
            "enable_thinking": config.model.enable_thinking,
            "include_reasoning": config.model.include_reasoning,
            "temperature": config.model.temperature,
            "explorer_temperature": config.explorer_temperature,
            "client_max_retries": 0,
            "sdk_max_attempts": 1,
        },
        "prompt_status": lead_prompt.status,
        "prompt_hash": lead_prompt.content_hash,
        "shared_hashes": lead_prompt.shared_hashes,
        "knowledge_hashes": {
            **lead_prompt.module_hashes,
            **(
                {module.module_id: module.content_hash for module in knowledge.modules}
                if knowledge
                else {}
            ),
        },
        "role_prompts": {
            role: {
                "content_hash": prompt.content_hash,
                "shared_hashes": prompt.shared_hashes,
                "module_hashes": prompt.module_hashes,
                "status": prompt.status,
            }
            for role, prompt in prompts.items()
        },
        "limits": {
            "max_generations": config.max_generations,
            "max_expert_consultations": config.max_expert_consultations,
            "analyst_concurrency": config.analyst_concurrency,
            "max_batch_jobs": config.max_batch_jobs,
            "deadline_seconds": config.deadline_seconds,
            "finalization": config.finalization.model_dump(mode="json"),
        },
    }
    asymmetric_results: list[ResearchResult] = []
    asymmetric_invocations = 0
    asymmetric_generations = config.max_generations if config.council_enabled else 32
    asymmetric_seconds = config.deadline_seconds if config.council_enabled else 2700
    pending_asymmetric: set[asyncio.Task[Any]] = set()
    if config.asymmetric_enabled:
        from wheel_team.asymmetric import PUBLIC_TOOLS

        asymmetric_prompt = (
            Path(__file__).parent / "prompts/asymmetric_opportunities.md"
        ).read_text()
        paths["asymmetric"] = str(run_dir / "asymmetric-results.json")
        paths["expert_inputs"] = str(run_dir / "expert-inputs.json")
        _write_json(Path(paths["asymmetric"]), [])
        _write_json(Path(paths["expert_inputs"]), [])
        manifest["asymmetric_opportunities"] = {
            "prompt_sha256": hashlib.sha256(asymmetric_prompt.encode()).hexdigest(),
            "tools": sorted(
                PUBLIC_TOOLS | ({"fact_check"} if config.fact_check.enabled else set())
            ),
            "scope": "public research; no private portfolio or authority",
            "max_invocations": 1,
            "max_generations": asymmetric_generations,
            "deadline_seconds": asymmetric_seconds,
            "fact_check": {
                "enabled": config.fact_check.enabled,
                "mode": "author-directed existing checker",
                "max_calls": config.fact_check.max_assessments,
                "scope": "public originals only; no host review loop",
            },
        }
    _write_json(Path(paths["manifest"]), manifest)
    trace.emit("run_started", role="team_lead")
    children: set[asyncio.Task[Any]] = set()
    clients = []
    research_results: list[ResearchResult] = []
    expert_results: list[ExpertResult] = []
    specialist_results: list[dict[str, Any]] = []
    specialist_locks = {role: asyncio.Lock() for role in ("wheel_expert", "dalio", "buffett")}
    if config.council_enabled:
        paths["specialists"] = str(run_dir / "specialist-assessments.json")
        paths["lead_coordination"] = str(run_dir / "lead-coordination.json")
        _write_json(Path(paths["specialists"]), [])
        manifest["final_author"] = None if request.feedback_record_only else "portfolio_manager"
    expert_research_requests: list[dict[str, Any]] = []
    expert_research_rounds = 0
    expert_research_jobs = 0
    scheduled_research: dict[str, tuple[ResearchJob, asyncio.Task[Any]]] = {}
    pending_research: set[asyncio.Task[Any]] = set()
    explorer_results: list[ResearchResult] = []
    explorer_invocations = 0
    continuity_records: list[dict[str, Any]] = []
    continuity_briefs: list[dict[str, Any]] = []
    expert_inputs: list[dict[str, Any]] = []
    continuity_lock = asyncio.Lock()
    feedback_lead_response: str | None = None
    if config.continuity is not None:
        paths["continuity"] = str(run_dir / "continuity-consultations.json")
        paths["expert_inputs"] = str(run_dir / "expert-inputs.json")
        _write_json(Path(paths["continuity"]), [])
        _write_json(Path(paths["expert_inputs"]), [])
    _write_json(Path(paths["research"]), [])
    _write_json(Path(paths["expert"]), [])
    _write_json(Path(paths["expert_research"]), [])
    _write_json(Path(paths["explorer"]), [])

    if config.fact_check.enabled:
        paths["fact_checks"] = str(run_dir / "fact-checks")
        manifest["fact_check"] = {
            "max_calls": config.fact_check.max_assessments,
            "output_tokens": config.fact_check.max_output_tokens,
            "reasoning_effort": config.fact_check.reasoning_effort or config.model.reasoning_effort,
            "context_limit": config.fact_check.context_window_tokens,
            "prompt_identity": manifest["role_prompts"]["fact_checker"],
            "caller_amendment_hashes": amendment_hashes,
            "semantics": "Explicit author tool; completed is execution, not factual approval",
        }

    policy = config.finalization
    cutoffs = {
        "analyst": stop_at - policy.analyst_reserve_seconds
        if policy.analyst_reserve_seconds
        else None,
        "wheel_expert": stop_at - policy.expert_reserve_seconds
        if policy.expert_reserve_seconds
        else None,
    }
    ceilings = {
        "analyst": config.max_generations - policy.analyst_reserve_generations
        if policy.analyst_reserve_generations
        else None,
        "wheel_expert": config.max_generations - policy.expert_reserve_generations
        if policy.expert_reserve_generations
        else None,
    }

    for role in ("dalio", "buffett", "portfolio_manager"):
        cutoffs[role] = cutoffs["wheel_expert"]
        ceilings[role] = ceilings["wheel_expert"]

    def check_child_capacity(role: RoleName) -> None:
        check_budget(budget, trace)
        cutoff, ceiling = cutoffs.get(role), ceilings.get(role)
        if cutoff is not None and asyncio.get_running_loop().time() >= cutoff:
            raise InvocationLimitExceeded("time_reserve")
        if ceiling is not None and budget.generations >= ceiling:
            raise InvocationLimitExceeded("generation_reserve")

    def report_originals(role: RoleName) -> dict[str, Any]:
        originals: dict[str, Any] = {}
        portfolio = readable_portfolio(request)
        if portfolio is not None:
            originals["input:portfolio"] = portfolio
        if request.owner_context is not None:
            originals["input:owner_context"] = request.owner_context
        originals.update({brief["evidence_ref"]: brief for brief in continuity_briefs})
        for kind, rows in [
            ("analyst", research_results),
            ("explorer", explorer_results),
            ("asymmetric", asymmetric_results),
            ("specialist", specialist_results),
        ]:
            for n, row in enumerate(rows, 1):
                if kind == "specialist" and (
                    not row["result"] or (role != "portfolio_manager" and row["role"] != role)
                ):
                    continue
                originals[f"input:{kind}:{n}"] = (
                    row.model_dump(mode="json") if isinstance(row, BaseModel) else dict(row)
                )
        if parent is not None:
            originals["input:parent:report"] = parent["packet"]["prior_report"]
        return originals

    def research_view(result: ResearchResult, kind: str = "analyst") -> dict[str, Any]:
        rows = {
            "analyst": research_results,
            "explorer": explorer_results,
            "asymmetric": asymmetric_results,
        }[kind]
        # Identity lookup keeps refs stable even for equal envelopes/completion reordering.
        n = next(n for n, row in enumerate(rows, 1) if row is result)
        return {
            "job_id": result.job_id,
            "author_role": kind,
            "evidence_ref": f"input:{kind}:{n}",
            "outcome": result.outcome,
            "summary": result.summary,
            "error": result.error,
        }

    def specialist_view(entry: dict[str, Any]) -> dict[str, Any]:
        result = entry["result"] or {}
        return {
            "role": entry["role"],
            "invocation_id": entry["invocation_id"],
            "evidence_ref": entry["evidence_ref"],
            "outcome": entry["outcome"],
            "assessment": result.get("assessment", ""),
            "error": result.get("error"),
        }

    def create_role(
        role: RoleName, invocation_id: str, private_tools: dict[str, Any] | None = None
    ) -> tuple[Any, RoleContext]:
        model_config = config.role_models.get(role) or (
            config.portfolio_manager_model
            if role == "portfolio_manager" and config.portfolio_manager_model is not None
            else config.model
        )
        if role == "portfolio_manager" and config.portfolio_manager_reasoning_effort is not None:
            model_config = model_config.model_copy(
                update={"reasoning_effort": config.portfolio_manager_reasoning_effort}
            )
        if role == "explorer" and config.explorer_temperature is not None:
            model_config = model_config.model_copy(
                update={"temperature": config.explorer_temperature}
            )
        if deps.model_factory is not None:
            model = deps.model_factory(model_config, budget, trace)
        elif deps.role_factory is None:
            steps = None
            if role_scripts is not None:
                queue = role_scripts.get(role)
                if not queue:
                    raise ValueError("Development scripted role has no remaining invocation")
                steps = queue.popleft()
                if not isinstance(steps, list) or not all(isinstance(step, dict) for step in steps):
                    raise ValueError(
                        "Development scripted invocation requires a list of output steps"
                    )
            model, client = build_model(model_config, scripted_steps=steps)
            if client is not None:
                clients.append(client)
        else:
            model = None
        role_available = (
            explorer_available
            if role == "explorer"
            else public_available
            if role == "analyst"
            else available
        )
        tools = {
            name: value
            for name, value in role_available.items()
            if name in role_tools(role, config)
        }
        tools.update(deps.tool_overrides.get(role, {}))
        if role == "continuity":
            tools = private_tools or {}
        if role == "team_lead" and config.continuity is None:
            tools.pop("consult_continuity", None)
        # The boundary remains explicit for injected role substitutes as well.
        tools = {
            name: value
            for name, value in tools.items()
            if name in role_tools(role, config) or role == "explorer"
        }
        if request.review_kind == "explanation" and role != "continuity":
            tools = {
                name: value
                for name, value in tools.items()
                if name
                in {
                    "consult_expert",
                    "consult_portfolio_manager",
                    "consult_specialists",
                    "consult_continuity",
                    "finance_calculate",
                    "knowledge_read",
                    "evidence_read",
                    "evidence_find",
                }
            }
        elif request.review_kind != "full":
            tools.pop("explore", None)
        context = RoleContext(
            request=request.model_copy(
                update={
                    "question": "Public research assignment supplied separately.",
                    "portfolio": None,
                    "portfolio_text": None,
                    "owner_context": None,
                    "feedback_source_ref": None,
                    "feedback_source_attribution": None,
                    "feedback_context": None,
                    "feedback_record_only": False,
                    "parent_review": None,
                    "review_kind": "full",
                }
            )
            if role in {"analyst", "explorer"}
            else request,
            config=config,
            model=model,
            prompt=prompts[role],
            tools=tools,
            budget=budget,
            trace=trace,
            run_dir=run_dir,
            market_provider=deps.market_provider,
            research_backend=deps.research_backend,
            invocation_id=invocation_id,
            store=public_store if role in {"analyst", "explorer"} else store,
            monotonic_deadline=stop_at,
            invocation_deadline=cutoffs.get(role),
            generation_ceiling=ceilings.get(role),
            tool_override_names=frozenset(deps.tool_overrides.get(role, {})),
            prior_review=(
                {
                    key: value
                    for key, value in parent["packet"].items()
                    if key
                    in {
                        "parent_run_id",
                        "prior_completed_at",
                        "prior_completion_reason",
                        "final_author",
                        "freshness",
                        "prior_question",
                    }
                }
                | {"prior_report_ref": "input:parent:report"}
                | (
                    {"prior_report": parent["packet"]["prior_report"]}
                    if role == "portfolio_manager"
                    else {}
                )
            )
            if parent and config.council_enabled and role in PRIVATE_EXPERTS
            else parent["packet"]
            if parent and role in PRIVATE_EXPERTS | {"team_lead"}
            else None,
        )
        if config.council_enabled and role in PRIVATE_EXPERTS:
            original_read = context.tools.get("evidence_read")

            async def selected_read(
                record_ref: str, view: Literal["compact"] = "compact"
            ) -> dict[str, Any]:
                """Read selected original input report refs or existing evidence records.

                input:analyst:N, input:explorer:N, input:asymmetric:N and authorized
                input:specialist:N identify saved exact envelopes. input:parent:report
                is the dated prior final report. Large envelopes return labeled incomplete
                JSON excerpts; use original refs for fact_check. Other refs use evidence_read.
                """
                if view != "compact":
                    return {"status": "invalid_data", "reason": 'Use view="compact"'}
                originals = report_originals(role)
                if record_ref in originals:
                    return {
                        "status": "success",
                        "record_ref": record_ref,
                        "record": bounded_record_view(originals[record_ref]),
                    }
                if record_ref.startswith("input:"):
                    return {
                        "status": "unavailable",
                        "reason": "Original unavailable in this role scope",
                    }
                return await original_read(record_ref, view=view)

            if original_read is not None:
                context.tools["evidence_read"] = selected_read
        if role in PRIVATE_EXPERTS:

            async def bound_research(jobs: list[ResearchJob]) -> dict[str, Any]:
                """Request focused PUBLIC analyst research; originals are reused across authors."""
                return await request_research(jobs, author_role=role, invocation_id=invocation_id)

            if "request_research" in context.tools:
                context.tools["request_research"] = bound_research
        if role == "portfolio_manager" and "consult_continuity" in context.tools:

            async def manager_continuity(
                message: str, access: Literal["read"] = "read"
            ) -> dict[str, Any]:
                """Read relevant attributed private Continuity context; no memory writes."""
                if access != "read":
                    raise ValueError("Portfolio Manager Continuity access is read-only")
                return await consult_continuity(message, access="read")

            context.tools["consult_continuity"] = manager_continuity
        context.fact_check_amendment = amendments.get(role, "")
        # Remove unauthorized injected checker bindings too, including Explorer overrides.
        context.tools.pop("fact_check", None)
        if config.fact_check.enabled and role in CHECKED_AUTHORS:
            checker_config = config.model.model_copy(
                update={
                    "max_output_tokens": config.fact_check.max_output_tokens,
                    "reasoning_effort": config.fact_check.reasoning_effort
                    or config.model.reasoning_effort,
                }
            )

            def checker_model() -> Any:
                if deps.model_factory is not None:
                    return deps.model_factory(checker_config, budget, trace)
                steps = None
                if role_scripts is not None:
                    queue = role_scripts.get("fact_checker")
                    if not queue:
                        raise ValueError("No remaining checker script")
                    steps = queue.popleft()
                model, client = build_model(checker_config, scripted_steps=steps)
                if client is not None:
                    clients.append(client)
                return model

            private_inputs: dict[str, Any] = {}
            if role in PRIVATE_EXPERTS:
                portfolio = readable_portfolio(request)
                if portfolio is not None:
                    private_inputs["input:portfolio"] = {
                        "body": portfolio,
                        "metadata": {"scope": "private", "source": "original supplied portfolio"},
                    }
                if request.owner_context is not None:
                    private_inputs["input:owner_context"] = {
                        "body": request.owner_context,
                        "metadata": {
                            "scope": "private",
                            "source": "original supplied owner context",
                        },
                    }
                for brief in continuity_briefs:
                    private_inputs[brief["evidence_ref"]] = {
                        "body": brief,
                        "metadata": {
                            "scope": "private",
                            "source": (
                                "dated derived continuity brief, "
                                "not authenticated underlying history"
                            ),
                        },
                    }
            checker_context = replace(
                context,
                prompt=prompts["fact_checker"],
                tools={},
                invocation_id=invocation_id,
                fact_check_amendment="",
            )
            session = ReasoningCheckSession(
                run_dir / "fact-checks" / hashlib.sha256(invocation_id.encode()).hexdigest()[:24],
                checker_context,
                checker_model,
                author_role=role,
                counter=token_counter,
                max_calls=config.fact_check.max_assessments,
                output_tokens=config.fact_check.max_output_tokens,
                context_limit=config.fact_check.context_window_tokens,
                private_inputs=private_inputs or None,
            )

            async def fact_check(
                message: str,
                evidence_refs: list[str] = [],  # noqa: B006 - advertised [] default, never mutated
                assumptions: list[str] = [],  # noqa: B006 - advertised [] default, never mutated
            ) -> dict[str, Any]:
                """Ask a fresh reasoning checker about your exact message and selected originals.

                Select only relevant evidence refs. Label unverified assumptions explicitly.
                Feedback is natural language; completed reports execution, not factual approval.
                Repair material errors; recheck materially changed claims when useful.
                Max three calls.
                """
                # Later terminal/reserve transitions must also bound explicit checker calls.
                session.context = replace(
                    context,
                    prompt=prompts["fact_checker"],
                    tools={},
                    fact_check_amendment="",
                )
                if role in PRIVATE_EXPERTS:
                    for kind, originals in [
                        ("analyst", research_results),
                        ("explorer", explorer_results),
                        ("asymmetric", asymmetric_results),
                        ("specialist", specialist_results),
                    ]:
                        for n, original in enumerate(originals, 1):
                            if kind == "specialist" and (
                                not original["result"]
                                or (role != "portfolio_manager" and original["role"] != role)
                            ):
                                continue
                            private_inputs["input:" + kind + ":" + str(n)] = {
                                "body": original.model_dump(mode="json")
                                if isinstance(original, BaseModel)
                                else dict(original),
                                "metadata": {
                                    "scope": "private",
                                    "source": "original "
                                    + kind
                                    + " report envelope; preserve outcome/author",
                                },
                            }
                    for brief in continuity_briefs:
                        private_inputs[brief["evidence_ref"]] = {
                            "body": brief,
                            "metadata": {
                                "scope": "private",
                                "source": "dated derived Continuity brief; not underlying notes",
                            },
                        }
                    if parent is not None and config.council_enabled:
                        private_inputs["input:parent:report"] = {
                            "body": parent["packet"]["prior_report"],
                            "metadata": {"scope": "private", "source": "dated prior final report"},
                        }
                    session.private_inputs = private_inputs or None
                reply = await session.check(message, evidence_refs, assumptions)
                if config.council_enabled:
                    return {key: reply[key] for key in ("status", "feedback", "error", "call_id")}
                return reply

            context.tools["fact_check"] = fact_check
        agent = (
            deps.role_factory(role, context)
            if deps.role_factory is not None
            else {
                "team_lead": build_team_lead,
                "analyst": build_analyst,
                "explorer": build_explorer,
                "wheel_expert": build_wheel_expert,
                "dalio": build_dalio,
                "buffett": build_buffett,
                "portfolio_manager": build_portfolio_manager,
                "continuity": build_continuity,
            }[role](context)
        )
        if isinstance(agent, Agent):
            specs = agent.tool_registry.get_all_tools_config()
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
            if config.child_output_mode == "typed" and role != "continuity":
                output_tool = StructuredOutputTool(output_types[role])
                output_spec = output_tool.tool_spec
                if role == "team_lead":
                    output_spec = project_team_result_spec(output_spec)
                    # Match the SDK's registration-time normalization before advertisement.
                    agent.tool_registry.validate_tool_spec(output_spec)
                specs[output_tool.tool_name] = output_spec
            manifest.setdefault("invocation_tool_specs", {})[invocation_id] = specs
            manifest.setdefault("tool_specs", {})[role] = specs
            manifest["tool_specs_hash"] = hashlib.sha256(
                json.dumps(
                    manifest["tool_specs"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            _write_json(Path(paths["manifest"]), manifest)
        return agent, context

    def track(coroutine: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        children.add(task)
        task.add_done_callback(children.discard)
        return task

    def save_child(path: Path, value: Any) -> None:
        try:
            _write_json(path, value)
        except OSError:
            if config.child_output_mode != "direct_text":
                raise
            trace.io_failures += 1

    async def research_one(job: ResearchJob) -> ResearchResult:
        local_timeout = asyncio.timeout_at(cutoffs["analyst"])
        try:
            # Knowing a private reference is not authorization to delegate it.
            for ref in job.evidence_refs:
                public_store.get(ref)
            # One absolute cutoff includes queued time; the awaiting lead owns no permit.
            async with local_timeout:
                async with budget.analyst_semaphore:
                    check_child_capacity("analyst")
                    agent_cutoff = cutoffs["analyst"] or stop_at
                    agent_ceiling = ceilings["analyst"] or config.max_generations
                    research_cutoff = agent_cutoff - policy.analyst_terminal_seconds
                    research_ceiling = agent_ceiling - bool(policy.analyst_terminal_seconds)
                    if policy.analyst_terminal_seconds:
                        # Queued/late work cannot claim the terminal reserve without research.
                        if asyncio.get_running_loop().time() >= research_cutoff:
                            raise InvocationLimitExceeded("time_reserve")
                        if budget.generations >= research_ceiling:
                            raise InvocationLimitExceeded("generation_reserve")
                    agent, context = create_role(
                        "analyst", "analyst:" + job.job_id + ":" + uuid4().hex[:8]
                    )
                    if config.child_output_mode == "direct_text":
                        text, partial = await _invoke_child_text(agent, job.model_dump_json())
                        value = ResearchResult(
                            job_id=job.job_id,
                            outcome="partial" if partial else "success",
                            summary=text,
                            error="Analyst text cut off at max_tokens" if partial else None,
                        )
                    elif policy.analyst_terminal_seconds:
                        context.invocation_deadline = research_cutoff
                        context.generation_ceiling = research_ceiling
                        research_timeout = asyncio.timeout_at(research_cutoff)
                        research_task = asyncio.create_task(
                            _invoke(agent, job.model_dump_json(), ResearchResult)
                        )
                        boundary = None
                        try:
                            async with research_timeout:
                                value = await asyncio.shield(research_task)
                        except BaseException as exc:
                            if not research_task.done() and not research_task.cancelling():
                                research_task.cancel()
                            # Retain ownership through every further outer/user cancellation.
                            # Only the SDK task's own terminal state ends cleanup waiting.
                            while not research_task.done():
                                try:
                                    await asyncio.shield(research_task)
                                except asyncio.CancelledError:
                                    continue
                                except Exception:
                                    break  # Inspect the completed task's failure below.
                            owner = asyncio.current_task()
                            if isinstance(exc, asyncio.CancelledError) or (
                                owner is not None and owner.cancelling()
                            ):
                                raise asyncio.CancelledError from None
                            if not research_task.cancelled():
                                cleanup_error = research_task.exception()
                                if cleanup_error is not None and cleanup_error is not exc:
                                    raise cleanup_error from None
                            boundary = (
                                "time_reserve"
                                if isinstance(exc, TimeoutError) and research_timeout.expired()
                                else invocation_failure(exc)
                            )
                            if boundary not in {"time_reserve", "generation_reserve"}:
                                raise
                        if boundary is not None:
                            # Research has awaited SDK/tool cleanup before reaching here.
                            # External cancellation must win even when it races that cleanup.
                            task = asyncio.current_task()
                            if task is not None and task.cancelling():
                                raise asyncio.CancelledError from None
                            check_child_capacity("analyst")
                            if context.output_repairs_exhausted:
                                raise InvocationLimitExceeded("output_repairs") from None
                            context.terminal_only = True
                            context.invocation_deadline = agent_cutoff
                            context.generation_ceiling = min(agent_ceiling, budget.generations + 1)
                            trace.emit(
                                "analyst_terminal_started",
                                job_id=job.job_id,
                                boundary=boundary,
                                history="actual_agent",
                                research_cutoff=research_cutoff,
                                analyst_cutoff=agent_cutoff,
                                generation_ceiling=context.generation_ceiling,
                            )
                            terminal_prompt = json.dumps(
                                {
                                    "phase": "analyst_terminal_finalization",
                                    "job": job.model_dump(mode="json"),
                                    "instruction": (
                                        "Return ResearchResult using only actual completed "
                                        "observations in this conversation and their canonical "
                                        "persisted references. Research tools are disabled. "
                                        "Preserve source units and limitations; do not infer "
                                        "missing facts or recover unfinished tool results."
                                    ),
                                }
                            )
                            value = await _invoke(
                                agent, terminal_prompt, ResearchResult, terminal=True
                            )
                    else:
                        value = await _invoke(agent, job.model_dump_json(), ResearchResult)
                    result = ResearchResult.model_validate(value).model_copy(
                        update={"job_id": job.job_id}
                    )
        except asyncio.CancelledError:
            result = ResearchResult(
                job_id=job.job_id, outcome="cancelled", error="Analyst cancelled"
            )
            research_results.append(result)
            save_child(
                Path(paths["research"]), [r.model_dump(mode="json") for r in research_results]
            )
            raise
        except Exception as exc:
            limit = budget_failure(exc)
            local_limit = (
                "time_reserve"
                if isinstance(exc, TimeoutError) and local_timeout.expired()
                else invocation_failure(exc)
            )
            result = ResearchResult(
                job_id=job.job_id,
                outcome="budget_exhausted" if limit or local_limit else "failed",
                error=(
                    "Analyst stopped at "
                    + local_limit
                    + "; capacity is reserved for later roles. Saved evidence remains available."
                )
                if local_limit
                else ("Run " + limit + " limit exhausted")
                if limit
                else "Analyst failed (" + type(exc).__name__ + ")",
            )
        research_results.append(result)
        save_child(Path(paths["research"]), [r.model_dump(mode="json") for r in research_results])
        trace.emit("analyst_completed", job_id=job.job_id, outcome=result.outcome)
        return result

    async def research_batch(jobs: list[ResearchJob]) -> list[dict[str, Any]]:
        """Await independent analyst jobs and return individual successes or failures."""
        # Strands dumps its validated Pydantic inputs back to dictionaries.
        parsed = [ResearchJob.model_validate(job) for job in jobs]
        if len(parsed) > config.max_batch_jobs:
            raise ValueError("Research batch exceeds configured max_batch_jobs")
        if len({job.job_id for job in parsed}) != len(parsed):
            raise ValueError("Research batch job_id values must be distinct")
        tasks = [schedule_research(job) for job in parsed]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return [
            research_view(result) if config.council_enabled else result.model_dump(mode="json")
            for result in results
        ]

    def research_key(job: ResearchJob) -> str:
        return hashlib.sha256(
            json.dumps([" ".join(job.question.split()), sorted(job.evidence_refs)]).encode()
        ).hexdigest()

    def schedule_research(job: ResearchJob) -> asyncio.Task[Any]:
        task = track(research_one(job))
        pending_research.add(task)
        task.add_done_callback(pending_research.discard)
        scheduled_research.setdefault(research_key(job), (job, task))
        return task

    async def request_research(
        jobs: list[ResearchJob],
        *,
        author_role: RoleName = "wheel_expert",
        invocation_id: str = "expert",
    ) -> dict[str, Any]:
        """Request focused PUBLIC analyst research within this same Expert assessment.

        Uses the shared run deadline, generation budget and analyst concurrency.
        No Lead or Expert is invoked recursively. Unchanged public questions and
        references reuse original pending/completed envelopes; their job IDs remain
        original and request_mapping explains reuse. Limits/partial errors are explicit.
        """
        nonlocal expert_research_rounds, expert_research_jobs
        parsed = [ResearchJob.model_validate(job) for job in jobs]
        expert_research_rounds += 1
        response: dict[str, Any] = {
            "status": "limit_reached",
            "author_role": author_role,
            "invocation_id": invocation_id,
            "reports": [],
            "request_mapping": [],
            "limitations": [],
        }
        expert_research_requests.append(response)
        new_keys = {research_key(job) for job in parsed} - scheduled_research.keys()
        if (
            expert_research_rounds > config.max_expert_research_rounds
            or len(parsed) > config.max_batch_jobs
            or expert_research_jobs + len(new_keys) > config.max_expert_research_jobs
        ):
            response["limitations"] = [
                "Expert research call/job limit reached; no new work launched"
            ]
        else:
            try:
                check_child_capacity(author_role)
            except Exception as exc:
                if not (budget_failure(exc) or invocation_failure(exc)):
                    raise
                response["limitations"] = [
                    "Shared run or Expert budget exhausted; no new work launched"
                ]
                save_child(Path(paths["expert_research"]), expert_research_requests)
                return response
            tasks = []
            for job in parsed:
                key = research_key(job)
                reused = key in scheduled_research
                if not reused:
                    schedule_research(job)
                    expert_research_jobs += 1
                original, task = scheduled_research[key]
                tasks.append(task)
                response["request_mapping"].append(
                    {
                        "requested_job_id": job.job_id,
                        "original_job_id": original.job_id,
                        "reused": reused,
                    }
                )
            response["status"] = "pending"
            save_child(Path(paths["expert_research"]), expert_research_requests)
            try:
                results = await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                response["status"] = "partial"
                response["limitations"] = [
                    "Additional research cancelled; saved original envelopes remain authoritative"
                ]
                save_child(Path(paths["expert_research"]), expert_research_requests)
                raise
            response["reports"] = [r.model_dump(mode="json") for r in results]
            response["status"] = (
                "completed" if all(r.outcome == "success" for r in results) else "partial"
            )
            response["limitations"] = [r.error for r in results if r.error]
        save_child(Path(paths["expert_research"]), expert_research_requests)
        trace.emit(
            "expert_research_returned",
            round=expert_research_rounds,
            new_jobs=expert_research_jobs,
            status=response["status"],
        )
        if config.council_enabled:
            return (
                {**response, "reports": [research_view(r) for r in results]}
                if response["reports"]
                else dict(response)
            )
        return response

    async def consult_continuity(
        message: str, access: Literal["read", "write"] = "read"
    ) -> dict[str, Any]:
        """Ask private Continuity to retrieve or maintain useful durable portfolio work.

        Supply the natural-language task. Host supplies actual current originals;
        read access has no write tool. Returns ordinary text and actual file receipts.
        """
        async with continuity_lock:
            response: dict[str, Any] = {
                "text": "",
                "completed": False,
                "read_refs": [],
                "writes": [],
                "error": None,
            }
            record: dict[str, Any] = {
                "message": message,
                "access": access,
                "at": deps.clock().isoformat(),
                "tools": [],
                "result": response,
            }
            files = None
            maintenance_receipt = None
            maintenance_identity = run_id + "-" + str(len(continuity_records) + 1)
            continuity_records.append(record)
            try:
                if (
                    config.continuity is None
                    or access not in {"read", "write"}
                    or not message.strip()
                ):
                    raise ContinuityError("INVALID_ARGUMENT", "Invalid or disabled consultation")
                check_budget(budget, trace)
                files = ContinuityFiles(config.continuity.root, writable=access == "write")
                errors = []

                def original(name: str, text: str) -> dict[str, Any]:
                    ref = None
                    try:
                        ref = files.save_source(run_id, name, text)
                    except (OSError, ContinuityError) as exc:
                        errors.append({"source": name, "error": type(exc).__name__})
                    return {"text": text, "source_ref": ref}

                originals: dict[str, Any] = {
                    "question": original("owner-question", request.question)
                }
                portfolio = readable_portfolio(request)
                if portfolio is not None:
                    originals["portfolio"] = original("portfolio", portfolio)
                if request.owner_context is not None:
                    originals["owner_context"] = original("owner-context", request.owner_context)
                if request.portfolio is not None:
                    originals["portfolio_snapshot"] = original(
                        "portfolio-snapshot", request.portfolio.model_dump_json()
                    )
                if parent is not None:
                    originals["prior_review"] = original(
                        "prior-review", json.dumps(parent["packet"], ensure_ascii=False)
                    )
                if request.feedback_source_ref is not None:
                    originals["owner_feedback_source"] = {
                        "source_ref": request.feedback_source_ref,
                        "attribution": (
                            (
                                request.feedback_source_attribution
                                or "Original recorded owner statement"
                            )
                            + "; recording time is not "
                            "observation freshness"
                        ),
                    }
                if request.feedback_context is not None:
                    originals["prior_owner_team_discussion"] = original(
                        "prior-owner-team-discussion", request.feedback_context
                    )
                if feedback_lead_response is not None:
                    originals["orchestrator_response"] = {
                        **original("orchestrator-response", feedback_lead_response),
                        "attribution": "Lead coordination response; not an Expert assessment",
                    }
                # Only actual completed assessment bodies, never a lead paraphrase.
                if access == "write":
                    originals["expert_assessments"] = [
                        {
                            **original("expert-assessment-" + str(n), result.assessment),
                            "error": result.error,
                            "author_role": "portfolio_manager"
                            if config.council_enabled
                            else "wheel_expert",
                            "authority": "final investment assessment",
                        }
                        for n, result in enumerate(expert_results, 1)
                        if result.assessment and not result.error
                    ]
                    if config.council_enabled:
                        originals["specialist_assessments"] = [
                            {
                                **original(
                                    "specialist-" + row["invocation_id"].replace(":", "-"),
                                    row["result"]["assessment"],
                                ),
                                "author_role": row["role"],
                                "outcome": row["outcome"],
                                "error": row["result"]["error"],
                                "authority": "supporting specialist judgment",
                            }
                            for row in specialist_results
                            if row["result"] and row["result"]["assessment"]
                        ]
                    supporting_sources = []
                    for kind, results in [
                        ("analyst", research_results),
                        ("explorer", explorer_results),
                    ]:
                        for n, result in enumerate(results, 1):
                            source = original(
                                kind + "-report-" + str(n),
                                result.summary,
                            )
                            supporting_sources.append(
                                {
                                    "kind": kind,
                                    "job_id": result.job_id,
                                    "outcome": result.outcome,
                                    "source_ref": source["source_ref"],
                                }
                            )
                else:
                    supporting_sources = []
                packet = {
                    "message": message,
                    "access": access,
                    "run_id": run_id,
                    "at": record["at"],
                    "originals": originals,
                    "source_errors": errors,
                    "supporting_sources": supporting_sources,
                    "unfinished_maintenance_attribution": (
                        "Historical failed invocation metadata for reconciliation, "
                        "not current owner instructions; later work may have addressed it."
                    ),
                    "unfinished_maintenance": files.unfinished_maintenance()
                    if access == "write"
                    else [],
                }
                if access == "write":
                    refs = []

                    def source_refs(value: Any) -> None:
                        if isinstance(value, dict):
                            if value.get("source_ref"):
                                refs.append(value["source_ref"])
                            for child in value.values():
                                source_refs(child)
                        elif isinstance(value, list):
                            for child in value:
                                source_refs(child)

                    source_refs(originals)
                    source_refs(supporting_sources)
                    maintenance_receipt = {
                        "message": message,
                        "at": record["at"],
                        "run_id": run_id,
                        "original_source_refs": refs,
                        "writes": [],
                        "completed": False,
                        "error": None,
                    }
                    files.save_maintenance(maintenance_identity, maintenance_receipt)

                def persist_consultation() -> None:
                    if maintenance_receipt is not None:
                        maintenance_receipt.update(
                            writes=list(files.writes),
                            completed=response["completed"],
                            error=response["error"],
                        )
                        files.save_maintenance(maintenance_identity, maintenance_receipt)
                    save_child(Path(paths["continuity"]), continuity_records)

                record["input"] = packet
                save_child(Path(paths["continuity"]), continuity_records)
                tools = continuity_tools(
                    files,
                    record["tools"],
                    persist_consultation,
                )
                agent, _ = create_role(
                    "continuity", "continuity:" + str(len(continuity_records)), tools
                )
                # Keep saved originals and the consultation audit complete; only
                # the model payload omits the parent's per-record audit ledgers.
                model_packet = packet
                if parent is not None:
                    model_packet = {
                        **packet,
                        "originals": {
                            **originals,
                            "prior_review": {
                                **originals["prior_review"],
                                "text": json.dumps(
                                    prompt_review(parent["packet"]), ensure_ascii=False
                                ),
                            },
                        },
                    }
                if config.council_enabled and access == "read":
                    # Originals are saved in Continuity's scoped immutable source area.
                    # Retrieval can read only the needed sources instead of inheriting all text.
                    model_packet = {
                        **packet,
                        "originals": {
                            name: {key: value for key, value in source.items() if key != "text"}
                            for name, source in originals.items()
                        },
                    }
                text, partial = await track(
                    _invoke_child_text(agent, json.dumps(model_packet, ensure_ascii=False))
                )
                response.update(
                    text=text, completed=not partial, error="max_tokens" if partial else None
                )
                if access == "read":
                    continuity_briefs.append(
                        {
                            "text": text,
                            "at": record["at"],
                            "attribution": (
                                "Private Continuity ordinary response; dated derived prior work, "
                                "not new owner instructions or refreshed facts. "
                                "References are audit "
                                "identifiers, not proof of original inspection."
                            ),
                            "evidence_ref": "input:continuity:" + str(len(continuity_briefs) + 1),
                            "completed": not partial,
                            "error": response["error"],
                        }
                    )
            except asyncio.CancelledError:
                response["error"] = "cancelled"
                raise
            except Exception as exc:
                response["error"] = (
                    str(exc) if isinstance(exc, ContinuityError) else type(exc).__name__
                )
            finally:
                if files is not None:
                    response.update(read_refs=list(files.read_refs), writes=list(files.writes))
                if files is not None and maintenance_receipt is not None:
                    try:
                        maintenance_receipt.update(
                            writes=list(files.writes),
                            completed=response["completed"],
                            error=response["error"],
                        )
                        files.save_maintenance(maintenance_identity, maintenance_receipt)
                    except ContinuityError as exc:
                        response.update(completed=False, error=str(exc))
                save_child(Path(paths["continuity"]), continuity_records)
            if config.council_enabled and access == "read":
                return {
                    "text": response["text"],
                    "completed": response["completed"],
                    "error": response["error"],
                    "evidence_ref": continuity_briefs[-1]["evidence_ref"]
                    if continuity_briefs and response["text"]
                    else None,
                }
            return response

    async def consult_specialists(requests: list[SpecialistRequest]) -> dict[str, Any]:
        """Consult Wheel, Dalio or Buffett peers, retaining their original prior assessments.

        Each request has role (wheel_expert, dalio or buffett), question and optional
        evidence_refs selecting relevant originals. Peers can read exact private inputs
        and reports on demand and request public research/checking. They
        cannot invoke other peers. Failed/partial originals retain their status.
        """
        parsed = [SpecialistRequest.model_validate(item) for item in requests]

        async def consult_one(item: SpecialistRequest) -> dict[str, Any]:
            role = item.role
            async with specialist_locks[role]:
                invocation_id = (
                    role + ":" + str(sum(row["role"] == role for row in specialist_results) + 1)
                )
                entry = {
                    "role": role,
                    "invocation_id": invocation_id,
                    "evidence_ref": "input:specialist:" + str(len(specialist_results) + 1),
                    "question": item.question,
                    "outcome": "failed",
                    "result": None,
                }
                specialist_results.append(entry)
                save_child(Path(paths["specialists"]), specialist_results)
                try:
                    check_child_capacity(role)
                    agent, _ = create_role(role, invocation_id)
                    packet = {
                        "question": item.question,
                        "selected_reports": {
                            ref: report_originals(role)[ref]
                            for ref in item.evidence_refs
                            if ref in report_originals(role)
                        },
                        "prior_specialist_assessments": [
                            specialist_view(row)
                            for row in specialist_results
                            if row is not entry and row["role"] == role
                        ],
                    }
                    if config.child_output_mode == "direct_text":
                        text, partial = await track(_invoke_child_text(agent, json.dumps(packet)))
                        result = ExpertResult(
                            assessment=text,
                            error="Specialist text cut off at max_tokens" if partial else None,
                        )
                    else:
                        result = ExpertResult.model_validate(
                            await track(_invoke(agent, json.dumps(packet), ExpertResult))
                        )
                        partial = bool(result.error)
                    entry.update(
                        outcome="partial" if partial else "success",
                        result=result.model_dump(mode="json"),
                    )
                except asyncio.CancelledError:
                    entry.update(
                        outcome="cancelled",
                        result=ExpertResult(assessment="", error="Specialist cancelled").model_dump(
                            mode="json"
                        ),
                    )
                    save_child(Path(paths["specialists"]), specialist_results)
                    raise
                except Exception as exc:
                    entry.update(
                        outcome="budget_exhausted"
                        if budget_failure(exc) or invocation_failure(exc)
                        else "failed",
                        result=ExpertResult(
                            assessment="", error="Specialist failed (" + type(exc).__name__ + ")"
                        ).model_dump(mode="json"),
                    )
                save_child(Path(paths["specialists"]), specialist_results)
                trace.emit(
                    "specialist_completed",
                    role=role,
                    invocation_id=invocation_id,
                    outcome=entry["outcome"],
                )
                return specialist_view(entry)

        # Different personas overlap; the lock above serializes only the same persona.
        tasks = [track(consult_one(item)) for item in parsed]
        try:
            reports = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return {
            "status": "completed"
            if all(row["outcome"] == "success" for row in reports)
            else "partial",
            "reports": reports,
        }

    async def consult_expert() -> dict[str, Any]:
        """Request the holistic expert review of the original owner task and all research.

        No arguments are needed. The host supplies the original run request, every
        completed run-local analyst envelope once, and separate explorer originals.
        Partial/failed envelopes retain their status and limitations. The original
        readable portfolio is already in the expert context. Do not transcribe or
        select reports, references, or financial narratives for this handoff.
        """
        if budget.expert_consultations >= budget.max_expert_consultations:
            return ExpertResult(
                assessment="Expert consultation limit reached",
                error="Expert consultation limit reached",
            ).model_dump(mode="json")
        check_budget(budget, trace)
        # Reserve once before yielding: concurrent calls cannot both pass the limit.
        budget.expert_consultations += 1
        authority_role = "portfolio_manager" if config.council_enabled else "wheel_expert"
        invocation_id = ("portfolio_manager:" if config.council_enabled else "expert:") + str(
            budget.expert_consultations
        )
        local_timeout = asyncio.timeout_at(cutoffs["wheel_expert"])
        try:
            async with local_timeout:
                if config.asymmetric_enabled and not config.council_enabled:
                    # SDK batch tool coroutines are scheduled together. Yield one
                    # turn so an Expert-first batch registers its specialist task.
                    # Await only actual registered tasks, never a future consultation.
                    await asyncio.sleep(0)
                    if pending_asymmetric:
                        await asyncio.gather(*list(pending_asymmetric))
                if pending_research and not config.council_enabled:
                    await asyncio.gather(*list(pending_research))
                if (
                    not config.council_enabled
                    and config.require_fresh_opportunities
                    and request.review_kind == "full"
                    and not explorer_results
                ):
                    await explore(
                        "Research fresh public investment opportunities and market context. "
                        "No candidate quota or obligation to endorse a trade."
                    )
                check_child_capacity(authority_role)
                agent, _ = create_role(authority_role, invocation_id)
                expert_input: dict[str, Any] = {
                    "question": request.question,
                    "analyst_reports": [r.model_dump(mode="json") for r in research_results],
                    "explorer_reports": [r.model_dump(mode="json") for r in explorer_results],
                    **(
                        {
                            "asymmetric_reports": [
                                r.model_dump(mode="json") for r in asymmetric_results
                            ]
                        }
                        if config.asymmetric_enabled
                        else {}
                    ),
                }
                if config.council_enabled:
                    expert_input = {"question": request.question}
                if config.continuity is not None:
                    expert_input["continuity_context"] = list(continuity_briefs)
                if config.continuity is not None or config.asymmetric_enabled:
                    expert_inputs.append(expert_input)
                    save_child(Path(paths["expert_inputs"]), expert_inputs)
                expert_question = json.dumps(expert_input)
                if config.child_output_mode == "direct_text":
                    text, partial = await track(_invoke_child_text(agent, expert_question))
                    result = ExpertResult(
                        assessment=text,
                        error="Expert text cut off at max_tokens" if partial else None,
                    )
                else:
                    response = await track(_invoke(agent, expert_question, ExpertResult))
                    result = ExpertResult.model_validate(response)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            limit = budget_failure(exc)
            local_limit = (
                "time_reserve"
                if isinstance(exc, TimeoutError) and local_timeout.expired()
                else invocation_failure(exc)
            )
            result = ExpertResult(
                assessment="Expert did not complete",
                error=("Expert stopped at " + local_limit + "; capacity is reserved for the lead.")
                if local_limit
                else ("Run " + limit + " limit exhausted")
                if limit
                else "Expert failed (" + type(exc).__name__ + ")",
            )
        expert_results.append(result)
        save_child(Path(paths["expert"]), [r.model_dump(mode="json") for r in expert_results])
        envelope = result.model_dump(mode="json")
        if config.child_output_mode == "direct_text":
            envelope["outcome"] = (
                "partial"
                if result.error == "Expert text cut off at max_tokens"
                else "failed"
                if result.error
                else "success"
            )
        return envelope

    def save_explorer(result: ResearchResult) -> None:
        explorer_results.append(result)
        save_child(Path(paths["explorer"]), [r.model_dump(mode="json") for r in explorer_results])
        trace.emit("explorer_completed", invocation_id=result.job_id, outcome=result.outcome)

    async def explore(question: str) -> dict[str, Any]:
        """Explore public candidates/context; original output reaches expert automatically.

        Final research tools and system prompt are owner-selected. This role does not
        automatically receive the private portfolio. The lead assigns public holdings
        research explicitly through research_batch after considering exploration.
        """
        nonlocal explorer_invocations
        check_budget(budget, trace)
        # Allocate before awaiting so overlapping invocations retain distinct identities.
        explorer_invocations += 1
        invocation_id = "explorer:" + str(explorer_invocations)
        trace.emit("explorer_started", invocation_id=invocation_id)
        try:
            agent, _ = create_role("explorer", invocation_id)
            if config.child_output_mode == "direct_text":
                text, partial = await track(_invoke_child_text(agent, question))
                result = ResearchResult(
                    job_id=invocation_id,
                    outcome="partial" if partial else "success",
                    summary=text,
                    error="Explorer text cut off at max_tokens" if partial else None,
                )
            else:
                value = await track(_invoke(agent, question, ResearchResult))
                result = ResearchResult.model_validate(value).model_copy(
                    update={"job_id": invocation_id}
                )
        except asyncio.CancelledError:
            save_explorer(
                ResearchResult(
                    job_id=invocation_id, outcome="cancelled", error="Explorer cancelled"
                )
            )
            raise
        except Exception as exc:
            limit = budget_failure(exc)
            result = ResearchResult(
                job_id=invocation_id,
                outcome="budget_exhausted" if limit else "failed",
                error="Run " + limit + " limit exhausted"
                if limit
                else "Explorer failed (" + type(exc).__name__ + ")",
            )
        save_explorer(result)
        return (
            research_view(result, "explorer")
            if config.council_enabled
            else result.model_dump(mode="json")
        )

    async def asymmetric_one(question: str, invocation_id: str) -> dict[str, Any]:
        from wheel_team.asymmetric import build_asymmetric
        from wheel_team.model_observer import CompletionTelemetry

        folder = run_dir / "asymmetric" / str(asymmetric_invocations)
        folder.mkdir(parents=True, mode=0o700)
        (folder / "task.md").write_text(question)
        (folder / "curriculum.md").write_text(asymmetric_prompt)
        trace.emit(
            "asymmetric_started",
            role="asymmetric_opportunities",
            invocation_id=invocation_id,
            question=question,
            prompt_sha256=manifest["asymmetric_opportunities"]["prompt_sha256"],
        )
        try:
            telemetry = CompletionTelemetry()
            if deps.model_factory is not None:
                model = deps.model_factory(config.model, budget, trace)
            else:
                model, client = build_model(config.model, telemetry=telemetry)
                if client is not None:
                    clients.append(client)
            checker_binding = None
            specialist_cutoff = (
                stop_at
                if config.council_enabled
                else min(stop_at, asyncio.get_running_loop().time() + asymmetric_seconds)
            )
            if config.fact_check.enabled:
                checker_config = config.model.model_copy(
                    update={
                        "max_output_tokens": config.fact_check.max_output_tokens,
                        "reasoning_effort": config.fact_check.reasoning_effort
                        or config.model.reasoning_effort,
                    }
                )

                def specialist_checker_model() -> Any:
                    if deps.model_factory is not None:
                        return deps.model_factory(checker_config, budget, trace)
                    steps = None
                    if role_scripts is not None:
                        queue = role_scripts.get("fact_checker")
                        if not queue:
                            raise ValueError("No remaining checker script")
                        steps = queue.popleft()
                    checker_model, checker_client = build_model(
                        checker_config, scripted_steps=steps
                    )
                    if checker_client is not None:
                        clients.append(checker_client)
                    return checker_model

                checker_context = RoleContext(
                    request=TeamRequest(question=question, mode="education"),
                    config=config,
                    model=model,
                    prompt=prompts["fact_checker"],
                    tools={},
                    budget=budget,
                    trace=trace,
                    run_dir=run_dir,
                    invocation_id=invocation_id,
                    store=public_store,
                    monotonic_deadline=stop_at,
                    invocation_deadline=specialist_cutoff,
                )
                checker_session = ReasoningCheckSession(
                    run_dir
                    / "fact-checks"
                    / hashlib.sha256(invocation_id.encode()).hexdigest()[:24],
                    checker_context,
                    specialist_checker_model,
                    author_role="asymmetric_opportunities",
                    counter=token_counter,
                    max_calls=config.fact_check.max_assessments,
                    output_tokens=config.fact_check.max_output_tokens,
                    context_limit=config.fact_check.context_window_tokens,
                )

                async def fact_check(
                    message: str,
                    evidence_refs: list[str] = [],  # noqa: B006 - advertised empty defaults
                    assumptions: list[str] = [],  # noqa: B006 - never mutated
                ) -> dict[str, Any]:
                    """Check your exact message against selected public originals.

                    Select relevant evidence refs and label unverified assumptions explicitly.
                    Feedback is natural language; completed reports execution, not factual approval.
                    Repair your claims and explicitly recheck when needed. Max three calls.
                    """
                    return await checker_session.check(message, evidence_refs, assumptions)

                checker_binding = fact_check
            agent, hooks = build_asymmetric(
                model=model,
                prompt=asymmetric_prompt,
                store=public_store,
                budget=budget,
                trace=trace,
                fact_check=checker_binding,
                fact_check_max_calls=config.fact_check.max_assessments,
                market_provider=deps.market_provider,
                research_backend=deps.research_backend,
                telemetry=telemetry,
                invocation_limits={
                    "max_generations": asymmetric_generations,
                    "deadline": (
                        budget.deadline
                        if config.council_enabled
                        else min(
                            budget.deadline, deps.clock() + timedelta(seconds=asymmetric_seconds)
                        )
                    ).isoformat(),
                },
            )
            (folder / "system-prompt.md").write_text(agent.system_prompt)
            hooks.invocation_id = invocation_id
            hooks.local_generation_limit = (
                None if config.council_enabled else asymmetric_generations
            )
            _write_json(folder / "tool-specs.json", agent.tool_registry.get_all_tools_config())
            async with asyncio.timeout_at(specialist_cutoff):
                text, partial = await track(_invoke_child_text(agent, question))
            result = ResearchResult(
                job_id=invocation_id,
                outcome="partial" if partial else "success",
                summary=text,
                error="Specialist text cut off at max_tokens" if partial else None,
            )
        except asyncio.CancelledError:
            result = ResearchResult(
                job_id=invocation_id, outcome="cancelled", error="Specialist cancelled"
            )
            asymmetric_results.append(result)
            save_child(
                Path(paths["asymmetric"]), [r.model_dump(mode="json") for r in asymmetric_results]
            )
            _write_json(folder / "result.json", result.model_dump(mode="json"))
            raise
        except Exception as exc:
            limit = budget_failure(exc)
            result = ResearchResult(
                job_id=invocation_id,
                outcome="budget_exhausted" if limit or isinstance(exc, TimeoutError) else "failed",
                error="Specialist stopped (" + type(exc).__name__ + ")",
            )
        asymmetric_results.append(result)
        save_child(
            Path(paths["asymmetric"]), [r.model_dump(mode="json") for r in asymmetric_results]
        )
        _write_json(folder / "result.json", result.model_dump(mode="json"))
        trace.emit(
            "asymmetric_completed",
            role="asymmetric_opportunities",
            invocation_id=invocation_id,
            outcome=result.outcome,
        )
        return result.model_dump(mode="json")

    async def consult_asymmetric(question: str) -> dict[str, Any]:
        """Ask the public asymmetric opportunity specialist for creative bounded-risk ideas.

        Supply a PUBLIC research task only; exclude account positions, private owner
        feedback, balances, orders and note excerpts. It has no portfolio authority.
        Original reports reach Wheel Expert automatically. One consultation per run.
        """
        nonlocal asymmetric_invocations
        if asymmetric_invocations >= 1:
            return {"outcome": "failed", "error": "Asymmetric consultation limit reached"}
        check_budget(budget, trace)
        asymmetric_invocations += 1
        invocation_id = "asymmetric:" + str(asymmetric_invocations)
        task = track(asymmetric_one(question, invocation_id))
        pending_asymmetric.add(task)
        try:
            result = await task
            if config.council_enabled:
                original = next(row for row in asymmetric_results if row.job_id == result["job_id"])
                return research_view(original, "asymmetric")
            return result
        finally:
            pending_asymmetric.discard(task)

    if config.asymmetric_enabled and request.review_kind == "full":
        available["consult_asymmetric"] = consult_asymmetric
    if config.explorer_enabled:
        available["explore"] = explore

    available.update(
        research_batch=research_batch,
        **(
            {"consult_portfolio_manager": consult_expert}
            if config.council_enabled
            else {"consult_expert": consult_expert}
        ),
        consult_specialists=consult_specialists,
        request_research=request_research,
    )
    if config.continuity is not None:
        available["consult_continuity"] = ContinuityTool(
            tool(consult_continuity),
            {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "minLength": 1},
                    "access": {"type": "string", "enum": ["read", "write"], "default": "read"},
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        )
    result: TeamResult
    native_lead_result = False
    try:
        async with asyncio.timeout_at(stop_at):
            lead = None
            if request.feedback_record_only:
                result = TeamResult(
                    summary="Owner feedback received; maintenance outcome is recorded separately."
                )
            elif not config.council_enabled:
                lead, _ = create_role("team_lead", "lead")
            if request.feedback_record_only:
                pass
            elif config.council_enabled:
                await consult_expert()
                result = TeamResult(summary="Portfolio Manager invoked directly by host.")
            elif config.child_output_mode == "direct_text":
                text, partial = await _invoke_child_text(lead, request.question)
                result = TeamResult(
                    summary=text,
                    report_markdown=text,
                    completion_reason="partial" if partial else "complete",
                )
            else:
                response = await _invoke(lead, request.question, TeamResult)
                result = TeamResult.model_validate(response)
                if not (result.report_markdown or result.summary).strip():
                    raise ValueError("Lead has no final narrative")
            native_lead_result = True
    except asyncio.CancelledError:
        result = TeamResult(
            completion_reason="cancelled",
            summary="Run cancelled before final synthesis.",
            warnings=("Run cancelled; available partial artifacts were preserved.",),
        )
    except TimeoutError:
        result = TeamResult(
            completion_reason="deadline",
            summary="Run deadline reached before final synthesis.",
            warnings=("Run deadline reached; available partial artifacts were preserved.",),
        )
    except Exception as exc:
        limit = budget_failure(exc)
        local_limit = invocation_failure(exc)
        reason = (
            "deadline" if limit == "deadline" else "partial" if limit or local_limit else "failed"
        )
        warning = (
            "Run " + limit + " limit exhausted."
            if limit
            else "Lead stopped at " + local_limit + "."
            if local_limit
            else "Run failed (" + type(exc).__name__ + ")."
        )
        result = TeamResult(
            completion_reason=reason,
            summary="Run stopped before final synthesis.",
            warnings=(warning, "Available partial artifacts were preserved."),
        )
        import traceback

        paths["failure"] = str(run_dir / "failure.json")
        save_child(
            Path(paths["failure"]),
            {
                "category": type(exc).__name__,
                "message": str(exc) if isinstance(exc, ValueError) else None,
                "frames": [
                    {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                    for frame in traceback.extract_tb(exc.__traceback__)
                ],
            },
        )
        trace.emit(
            "run_failed",
            category=type(exc).__name__,
            limit=limit or local_limit,
            message=str(exc) if isinstance(exc, ValueError) else None,
        )
    finally:
        if request.feedback_source_ref is not None:
            feedback_lead_response = result.summary
            paths["feedback_maintenance"] = str(run_dir / "feedback-maintenance.json")
            maintenance = {
                "text": "",
                "completed": False,
                "read_refs": [],
                "writes": [],
                "error": None,
            }
            try:
                if result.completion_reason == "cancelled":
                    maintenance["error"] = "cancelled; original feedback remains preserved"
                else:
                    async with asyncio.timeout_at(stop_at):
                        maintenance = await consult_continuity(
                            "Preserve the continuing owner feedback/discussion from the "
                            "supplied exact originals. "
                            "Owner reasons/preferences/decisions and Expert judgments have "
                            "distinct attribution; "
                            "Lead coordination text is not an Expert assessment. Unknown "
                            "execution stays unknown. "
                            "Choose useful Markdown organization and preserve disagreements "
                            "and uncertainties. "
                            "If Expert failed or was not needed, the original owner statement "
                            "still matters. "
                            "Report actual saved changes and remaining limitations.",
                            access="write",
                        )
            except Exception as exc:
                if continuity_records and continuity_records[-1]["access"] == "write":
                    maintenance = continuity_records[-1]["result"]
                maintenance["error"] = type(exc).__name__
            save_child(Path(paths["feedback_maintenance"]), maintenance)
        outstanding = list(children)
        for child in outstanding:
            child.cancel()
        if outstanding:
            await asyncio.gather(*outstanding, return_exceptions=True)
        for client in clients:
            await client.close()
    if config.council_enabled and not request.feedback_record_only:
        save_child(Path(paths["lead_coordination"]), result.model_dump(mode="json"))
        if expert_results:
            manager = expert_results[-1]
            if not manager.error:
                result = result.model_copy(
                    update={
                        "summary": manager.assessment,
                        "report_markdown": manager.assessment,
                        "completion_reason": result.completion_reason
                        if result.completion_reason in {"cancelled", "deadline"}
                        else "complete",
                    }
                )
                native_lead_result = True
            else:
                result = result.model_copy(
                    update={
                        "summary": manager.assessment,
                        "report_markdown": manager.assessment,
                        "completion_reason": result.completion_reason
                        if result.completion_reason in {"cancelled", "deadline"}
                        else "partial"
                        if manager.assessment
                        else "failed",
                        "warnings": (*result.warnings, manager.error),
                    }
                )
                native_lead_result = False
        else:
            result = result.model_copy(
                update={
                    "summary": "Portfolio Manager did not complete an assessment.",
                    "report_markdown": "",
                    "completion_reason": result.completion_reason
                    if result.completion_reason in {"cancelled", "deadline"}
                    else "partial",
                    "warnings": (
                        *result.warnings,
                        "No authoritative Portfolio Manager original is available.",
                    ),
                }
            )
            native_lead_result = False

    draft_text: str | None = None
    draft_metadata: dict[str, Any] = {}
    for metadata_path in run_dir.glob("unvalidated-lead-draft-*.json"):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        draft_path = metadata_path.with_suffix(".txt")
        generation = metadata["generation"]
        paths[f"unvalidated_lead_draft_{generation}"] = str(draft_path)
        paths[f"unvalidated_lead_draft_metadata_{generation}"] = str(metadata_path)
        if generation > draft_metadata.get("generation", 0):
            draft_text = draft_path.read_text(encoding="utf-8")
            draft_metadata = metadata
            paths["unvalidated_lead_draft"] = str(draft_path)
            paths["unvalidated_lead_draft_metadata"] = str(metadata_path)
    if draft_text is not None:
        if not native_lead_result:
            result = result.model_copy(
                update={
                    "warnings": result.warnings
                    + ("A saved model draft is unvalidated; native finalization did not complete.",)
                }
            )
    completed = deps.clock()
    if (
        config.require_fresh_opportunities
        and request.review_kind == "full"
        and not request.feedback_record_only
        and not explorer_results
        and result.completion_reason == "complete"
    ):
        result = result.model_copy(
            update={
                "completion_reason": "partial",
                "warnings": (
                    *result.warnings,
                    "Full review omitted required fresh opportunity research.",
                ),
            }
        )
    result = result.model_copy(
        update={
            "run_id": run_id,
            "mode": request.mode,
            "artifacts": paths,
            "started_at": started,
            "completed_at": completed,
            "model_identity": {
                "provider": config.model.provider,
                "model_id": config.model.model_id,
                "fixture_sha256": manifest["fixture_sha256"],
                "evaluation_mode": deps.evaluation_mode,
                "live_acceptance": False,
            },
            "prompt_identities": manifest["role_prompts"],
            "portfolio_snapshot": request.portfolio,
            "supporting_records": {},
            "owner_policy": config.policy,
            "lineage": lineage,
        }
    )
    if native_lead_result:
        # Successful lead text is the deliverable. Metadata is not a publication gate.
        report = result.report_markdown or result.summary
    else:
        if config.child_output_mode == "typed":
            result = preserve_partial_research(
                result, [*research_results, *asymmetric_results], expert_results
            )
        report = render_result(result)
    if draft_text is not None and not native_lead_result and not config.council_enabled:
        longest = max((len(run) for run in re.findall(r"`+", draft_text)), default=2)
        fence = "`" * max(3, longest + 1)
        report += (
            "\n## Unvalidated model draft\n\n"
            "Native TeamResult did not complete. This separate public response has not passed "
            "native validation or report integrity checks. Its claims are unverified.\n\n"
            f"Lead generation {draft_metadata['generation']}; "
            f"captured {draft_metadata['captured_at']}.\n\n"
            f"{fence}text\n{draft_text}\n{fence}\n"
        )
    result = result.model_copy(update={"report_markdown": report})
    Path(paths["report"]).write_text(report, encoding="utf-8")
    _write_json(Path(paths["result"]), result.model_dump(mode="json"))
    manifest.update(
        **dependency_context(deps.market_provider, deps.research_backend),
        completed_at=completed.isoformat(),
        completion_reason=result.completion_reason,
        generations=budget.generations,
        expert_consultations=budget.expert_consultations,
        expert_research_rounds=expert_research_rounds,
        expert_research_new_jobs=expert_research_jobs,
    )
    manifest["passive_io_failures"] = trace.io_failures
    _write_json(Path(paths["manifest"]), manifest)
    _write_json(
        Path(paths["review_checkpoint"]), {"run_id": run_id, "files": checkpoint_files(run_dir)}
    )
    trace.emit(
        "run_completed",
        completion_reason=result.completion_reason,
        generations=budget.generations,
        expert_consultations=budget.expert_consultations,
    )
    return result
