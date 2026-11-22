"""Small evaluation seam: isolated SDK roles, actual teams, ordinary review files."""

import asyncio
import copy
import inspect
import json
import re
import time
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from strands import ToolContext, tool
from strands.models import Model

from portfolio_management.agents import build_analyst, build_explorer, build_team_lead, build_wheel_expert
from portfolio_management.agents.base import ROLE_TOOLS
from portfolio_management.config import TeamConfig
from portfolio_management.contracts import AgentRole, MarketQuery, MarketResult
from portfolio_management.evaluation.compare_variants import content_hash
from portfolio_management.knowledge_loader import load_knowledge
from portfolio_management.models import build_model
from portfolio_management.prompt_loader import load_prompt
from portfolio_management.team import run_team
from portfolio_management.types import (
    ExpertResult,
    ResearchJob,
    ResearchResult,
    RoleContext,
    RunBudget,
    TeamDependencies,
    TeamRequest,
    TeamResult,
    TraceSink,
)

# Plain argument schemas mirror the thin callable boundary. No production handler is
# constructed or called by isolated role evaluation, including calculators and storage.
_ARGUMENTS = {
    "finance_calculate": (
        {"formula": "string", "inputs": "object", "parameters": "object"},
        ["formula", "inputs"],
    ),
    "portfolio_read": ({"snapshot_ref": "string"}, ["snapshot_ref"]),
    "knowledge_read": ({"module_id": "string"}, ["module_id"]),
    "evidence_read": ({"record_ref": "string"}, ["record_ref"]),
    "evidence_find": ({"kind": "string", "query": "object"}, ["kind"]),
    "market_query": ({"query": "object"}, ["query"]),
    "public_search": ({"query": "string", "limit": "integer"}, ["query"]),
    "fetch_sources": ({"urls": "array"}, ["urls"]),
    "market_statistics": (
        {"series": "array", "lookback_days": "integer", "max_observations": "integer"},
        ["series"],
    ),
    "options_liquidity": (
        {"symbol": "string", "expiries": "array", "strike_distance_pct": "number"},
        ["symbol"],
    ),
    "publish_idea": ({"idea": "object"}, ["idea"]),
    "publish_quality": ({"assessment": "object"}, ["assessment"]),
    "publish_review": ({"review": "object"}, ["review"]),
    "research_batch": ({"jobs": "array"}, ["jobs"]),
    "request_research": ({"jobs": "array"}, ["jobs"]),
    "explore": ({"question": "string"}, ["question"]),
    "consult_expert": ({}, []),
}


class CaseMarketProvider:
    """Evaluation-only finite synthetic world. Unknown requests are unavailable."""

    def __init__(self, fixtures: dict[str, Any]):
        self.pages = copy.deepcopy(fixtures.get("market_pages", {}))

    def capabilities(self) -> list[str]:
        return sorted(self.pages)

    async def query(self, query: MarketQuery) -> MarketResult:
        page = self.pages.get(query.capability)
        if page is None:
            return MarketResult(
                query_id=query.query_id,
                status="unavailable",
                reason="No controlled evaluation page",
            )
        # Pages may be keyed by a semantic request hash for precise availability.
        if "queries" in page:
            key = content_hash(query.model_dump(mode="json", exclude={"query_id"}))
            page = page["queries"].get(key)
            if page is None:
                return MarketResult(
                    query_id=query.query_id,
                    status="unavailable",
                    reason="No controlled evaluation request",
                )
        return MarketResult.model_validate({**page, "query_id": query.query_id})


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )


def _mock_tools(role: str, fixtures: dict[str, Any], trace: TraceSink) -> dict[str, Any]:
    unknown = set(fixtures) - ROLE_TOOLS[role]
    if unknown:
        raise ValueError("Mocked tools not exposed to role: " + ", ".join(sorted(unknown)))
    tools = {}
    for name in sorted(ROLE_TOOLS[role]):
        arguments, required = _ARGUMENTS[name]
        responses = copy.deepcopy(fixtures.get(name, []))
        if not isinstance(responses, (list, dict)):
            raise ValueError("Mock responses must be lists or argument-keyed fixture objects")

        def construct(
            tool_name: str, queue: Any, fields: dict[str, str], required: list[str]
        ) -> Any:
            async def controlled(**kwargs: Any) -> Any:
                """Return only controlled evaluation data; never call a production tool."""
                value: Any
                if isinstance(queue, dict):
                    matches = [
                        step["response"]
                        for step in queue.get("by_arguments", [])
                        if step["arguments"] == kwargs
                    ]
                    if not matches and "default" not in queue:
                        trace.emit(
                            "mock_tool_incompatible",
                            name=tool_name,
                            category="response_not_configured",
                        )
                    value = (
                        matches[0]
                        if matches
                        else queue.get(
                            "default",
                            {"status": "unavailable", "reason": "No controlled arguments match"},
                        )
                    )
                elif not queue:
                    trace.emit(
                        "mock_tool_incompatible", name=tool_name, category="response_not_configured"
                    )
                    value = {"status": "unavailable", "reason": "No controlled mock response"}
                else:
                    step = queue.pop(0)
                    if isinstance(step, dict) and "arguments" in step and "response" in step:
                        if kwargs != step["arguments"]:
                            trace.emit(
                                "mock_tool_incompatible",
                                name=tool_name,
                                category="arguments_mismatch",
                            )
                            raise ValueError("Controlled tool arguments differ from fixture")
                        value = step["response"]
                    else:
                        value = step
                if isinstance(value, dict) and "raise" in value:
                    trace.emit("mock_tool_failure", name=tool_name, category="controlled_failure")
                    raise RuntimeError("Controlled evaluation tool failure")
                trace.emit("mock_tool_result", name=tool_name, result=value)
                return value

            hints: dict[str, Any] = {
                "string": str,
                "object": dict[str, Any],
                "array": list[str],
                "integer": int,
                "number": float,
            }
            parameters = []
            for field, kind in fields.items():
                hint = list[ResearchJob] if field == "jobs" else hints[kind]
                default: Any = inspect.Parameter.empty
                if field == "limit":
                    default = 5
                elif field not in required:
                    hint, default = hint | None, None
                parameters.append(
                    inspect.Parameter(
                        field,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        annotation=hint,
                        default=default,
                    )
                )
            controlled.__name__ = tool_name
            controlled.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
            return tool(name=tool_name)(controlled)

        tools[name] = construct(name, responses, arguments, required)
    return tools


def _read_artifacts(paths: dict[str, str]) -> tuple[dict[str, Any], list[Any], list[Any]]:
    manifest = json.loads(Path(paths["manifest"]).read_text())
    trace_path = Path(paths["trace"])
    trace = (
        [json.loads(line) for line in trace_path.read_text().splitlines()]
        if trace_path.exists()
        else []
    )
    records = Path(paths["manifest"]).parent / "records"
    evidence = [json.loads(path.read_text()) for path in sorted(records.rglob("*.json"))]
    return manifest, trace, evidence


def evaluate_findings(
    case: dict[str, Any],
    result: dict[str, Any],
    trace: list[Any],
    evidence: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Detect integrity failures and waste; do not prescribe an investment trajectory."""
    findings: list[dict[str, Any]] = copy.deepcopy(result.get("findings", []))

    def note(code: str, reason: str, severity: str = "error") -> None:
        findings.append({"code": code, "severity": severity, "reason": reason})

    text = result.get("report_markdown") or result.get("summary") or result.get("assessment") or ""
    if not text.strip():
        note("final_output_missing", "No usable final answer was produced")
    completion = result.get("completion_reason", result.get("outcome", "complete"))
    if completion not in {"complete", "success"}:
        note("incomplete_output", f"Outcome is {completion}; preserve partial output")
    if (
        case.get("mode") in {"discovery", "combined"}
        and case.get("role", "team_lead") == "team_lead"
    ):
        slots = result.get("candidate_slots", [])
        if len(slots) != 5 and not any(
            f.get("code") == "candidate_slots_missing" for f in findings
        ):
            note(
                "candidate_slots_missing",
                "Discovery must account for five supported/unavailable slots",
            )
    calls = [
        row
        for row in trace
        if row.get("event") == "tool_call_started"
        and row.get("name") not in {"TeamResult", "ResearchResult", "ExpertResult"}
    ]
    counts = Counter(
        (row.get("invocation_id"), row.get("name"), content_hash(row.get("inputs", {})))
        for row in calls
    )
    if any(
        count > 1 and name in {"market_query", "research_batch"}
        for (_, name, _), count in counts.items()
    ):
        note(
            "redundant_research",
            "Repeated identical research call; review whether it added evidence",
            "warning",
        )
    if any(count >= 4 for count in counts.values()):
        note("looping", "At least four identical calls in one invocation", "warning")
    if any(
        row.get("event") == "mock_tool_failure"
        or (row.get("event") == "tool_call_finished" and row.get("status") == "error")
        for row in trace
    ):
        note("tool_failure", "Visible tool failure; review recovery and uncertainty", "warning")
    # Oracles are hand-derived fixture outputs, never generated by the calculator being checked.
    records = [row.get("record", row) for row in evidence or []]
    for oracle in case.get("financial_oracles", []):
        matches = [
            row
            for row in records
            if row.get("formula") == oracle["formula"]
            and all(
                str(row.get("inputs", {}).get(key)) == str(value)
                for key, value in oracle.get("match_inputs", {}).items()
            )
        ]
        if not matches:
            note("calculation_evidence_missing", f"No output for oracle {oracle['id']}")
        elif any(
            any(
                str(row.get("outputs", {}).get(key)) != str(value)
                for key, value in oracle["expected"].items()
            )
            for row in matches
        ):
            note("financial_oracle_mismatch", f"Independent arithmetic differs: {oracle['id']}")
    return findings


def categorize_outcomes(
    result: dict[str, Any], findings: list[dict[str, Any]], trace: list[Any], evidence: list[Any]
) -> dict[str, Any]:
    """Keep operational, fixture, judgment and evidence observations separate."""
    codes = {finding.get("code") for finding in findings}
    protocol_codes = codes & {
        "final_output_missing",
        "empty_result",
        "invalid_candidate_slot",
        "candidate_slots_missing",
    }
    protocol_events = [
        row.get("event")
        for row in trace
        if row.get("event") in {"role_failed", "run_failed"}
        or (
            row.get("event") in {"model_generation_finished", "tool_call_finished"}
            and row.get("status") in {"failed", "error"}
        )
    ]
    incompatible = [
        row.get("category", "unspecified")
        for row in trace
        if row.get("event") == "mock_tool_incompatible"
    ]
    grader_codes = codes & {
        "calculation_evidence_missing",
        "mock_fixture_missing",
        "grader_incompatible",
        "mock_arguments_mismatch",
    }
    fact_codes = codes & {
        "financial_oracle_mismatch",
        "calculation_mismatch",
        "unsupported_readiness_claim",
        "unresolved_reference",
        "invalid_support_reference",
        "invalid_quote_reference",
        "snapshot_mismatch",
        "quality_company_mismatch",
        "duplicate_company",
        "unsupported_fact",
        "unsupported_claim",
    }
    evidence_codes = {
        code
        for code in codes
        if isinstance(code, str)
        and (
            code.startswith(("quote_", "evidence_time_", "portfolio_freshness"))
            or code
            in {
                "stale_entry_quote",
                "evidence_not_current",
                "portfolio_unavailable",
                "readiness_unverified",
                "candidate_support_missing",
                "contract_economics_missing",
            }
        )
    }

    limitations: set[str] = set()

    def statuses(value: Any) -> list[str]:
        if isinstance(value, list):
            return [status for item in value for status in statuses(item)]
        if not isinstance(value, dict):
            return []
        found = [value["status"]] if isinstance(value.get("status"), str) else []
        if value.get("source_time_availability") in {"unknown", "unavailable"}:
            limitations.add("source_time_unverified")
        if value.get("timezone_state") == "unknown":
            limitations.add("source_timezone_unresolved")
        if value.get("delay_state") in {"delayed", "end_of_day", "unknown"}:
            limitations.add("source_delay_or_freshness_requires_review")
        for key in ("data", "json"):
            found += statuses(value.get(key))
        if isinstance(value.get("text"), str):
            try:
                found += statuses(json.loads(value["text"]))
            except (ValueError, TypeError):
                pass
        return found

    unavailable = {
        status
        for row in trace
        for status in statuses(row.get("result"))
        if status in {"unavailable", "unknown", "transient_error", "permanent_error"}
    }
    unavailable |= {
        status
        for row in evidence
        for status in statuses(row.get("record", row))
        if status in {"unavailable", "unknown", "transient_error", "permanent_error"}
    }
    completion = result.get("completion_reason", result.get("outcome", "complete"))
    terminal_protocol = completion in {"failed", "deadline", "cancelled", "budget_exhausted"}
    text = (
        result.get("report_markdown")
        or result.get("summary")
        or result.get("assessment")
        or result.get("final_response")
        or ""
    )
    assessable = (
        bool(text.strip()) and not terminal_protocol and not incompatible and not grader_codes
    )
    return {
        "execution_protocol": {
            "status": "observed"
            if terminal_protocol or protocol_codes or protocol_events
            else "not_observed",
            "finding_codes": sorted(protocol_codes),
            "events": protocol_events,
            "error_category": result.get("error_category"),
        },
        "mock_grader_compatibility": {
            "status": "observed" if incompatible or grader_codes else "not_observed",
            "finding_codes": sorted(grader_codes),
            "events": incompatible,
        },
        "facts_investment_reasoning": {
            "status": "unknown"
            if not assessable
            else "observed"
            if fact_codes
            else "pending_review",
            "finding_codes": sorted(fact_codes),
            "judgment": None,
        },
        "evidence_availability": {
            "status": "observed"
            if evidence_codes or unavailable or limitations
            else "not_observed",
            "finding_codes": sorted(evidence_codes),
            "tool_statuses": sorted(unavailable),
            "source_limitations": sorted(limitations),
        },
    }


async def _run_role(
    case: dict[str, Any], request: TeamRequest, config: TeamConfig, deps: TeamDependencies
) -> tuple[dict[str, Any], dict[str, str]]:
    role = case.get("role", "team_lead")
    prompt = load_prompt(AgentRole(role), config.prompts)
    knowledge = (
        load_knowledge(config.knowledge_manifest_path) if config.knowledge_manifest_path else None
    )
    if knowledge and knowledge.status != "approved" and config.prompts.mode != "development":
        raise ValueError("Development knowledge requires explicit development selection")
    started = deps.clock()
    if started.utcoffset() is None:
        raise ValueError("Evaluation clock must be timezone-aware")
    budget = RunBudget(
        config.max_generations,
        config.max_expert_consultations,
        started + timedelta(seconds=config.deadline_seconds),
        asyncio.Semaphore(config.analyst_concurrency),
    )
    run_id = "role-" + uuid4().hex
    run_dir = config.output_dir.expanduser().resolve() / run_id
    # Model validation happens before writing a run directory.
    client = None
    role_model_config = config.model
    if role == "explorer" and config.explorer_temperature is not None:
        role_model_config = config.model.model_copy(
            update={"temperature": config.explorer_temperature}
        )
    if deps.model_factory:
        model = deps.model_factory(
            role_model_config, budget, TraceSink(run_dir / "trace.jsonl", deps.clock)
        )
    else:
        model, client = build_model(role_model_config)
    if not isinstance(model, Model):
        raise ValueError("Role evaluation requires an actual SDK model transport")
    run_dir.mkdir(parents=True, mode=0o700)
    (run_dir / "records").mkdir(mode=0o700)
    paths = {
        name: str(run_dir / filename)
        for name, filename in {
            "request": "request.json",
            "manifest": "manifest.json",
            "trace": "trace.jsonl",
            "result": "result.json",
            "report": "report.md",
        }.items()
    }
    trace = TraceSink(Path(paths["trace"]), deps.clock)
    manifest = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "evaluation_mode": "role",
        "model_settings": {"temperature": role_model_config.temperature},
        "role": role,
        "tools_mocked": "all",
        "live_acceptance": False,
        "prompt_status": prompt.status,
        "prompt_hashes": {role: prompt.content_hash, **prompt.shared_hashes},
        "knowledge_hashes": {
            **prompt.module_hashes,
            **(
                {module.module_id: module.content_hash for module in knowledge.modules}
                if knowledge
                else {}
            ),
        },
    }
    _write(Path(paths["request"]), request.model_dump(mode="json"))
    _write(Path(paths["manifest"]), manifest)
    context = RoleContext(
        request,
        config,
        model,
        prompt,
        _mock_tools(
            role, case.get("mocked_tools", case.get("role_mocks", {}).get(role, {})), trace
        ),
        budget,
        trace,
        run_dir,
    )
    schemas: dict[str, type[BaseModel]] = {
        "team_lead": TeamResult,
        "analyst": ResearchResult,
        "explorer": ResearchResult,
        "wheel_expert": ExpertResult,
    }
    schema = schemas[role]
    try:
        agent = {
            "team_lead": build_team_lead,
            "analyst": build_analyst,
            "explorer": build_explorer,
            "wheel_expert": build_wheel_expert,
        }[role](context)
        async with asyncio.timeout(config.deadline_seconds):
            response = await agent.invoke_async(request.question, structured_output_model=schema)
        result = schema.model_validate(response.structured_output).model_dump(mode="json")
    except Exception as exc:
        result = {
            "summary": "",
            "completion_reason": "failed",
            "error_category": type(exc).__name__,
        }
        trace.emit("role_failed", category=type(exc).__name__)
    finally:
        if client:
            await client.close()
    manifest.update(completed_at=deps.clock().isoformat(), generations=budget.generations)
    if "agent" in locals():
        specs = agent.tool_registry.get_all_tool_specs()
        manifest.update(tool_specs={role: specs}, tool_specs_hash=content_hash({role: specs}))
    result.update(run_id=run_id)
    _write(Path(paths["result"]), result)
    _write(Path(paths["manifest"]), manifest)
    Path(paths["report"]).write_text(
        result.get("report_markdown") or result.get("summary") or result.get("assessment") or "",
        encoding="utf-8",
    )
    return result, paths


async def run_case(
    case: dict[str, Any], config: TeamConfig, deps: TeamDependencies, mode: str
) -> dict[str, Any]:
    case = copy.deepcopy(case)
    if not isinstance(case.get("case_id"), str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:-]*", case["case_id"]
    ):
        raise ValueError("case_id must be a nonempty logical identifier")
    if mode not in ("scripted", "role", "team"):
        raise ValueError("Evaluation mode must be scripted, role or team")
    if (mode == "scripted") != (config.model.provider == "scripted"):
        raise ValueError("Evaluation mode must describe the actual inference provider")
    if case.get("role", "team_lead") not in ROLE_TOOLS:
        raise ValueError("Unknown evaluation role")
    if mode != "role" and case.get("role", "team_lead") != "team_lead":
        raise ValueError("Team/scripted evaluation starts the team_lead; use role for isolation")
    if mode != "scripted" and deps.role_factory is not None:
        raise ValueError("role_factory substitution is scripted evidence only")
    if mode != "scripted" and deps.tool_overrides:
        raise ValueError("tool_overrides forbidden: role uses all mocks; team uses actual tools")
    if mode == "team" and case.get("mocked_tools"):
        raise ValueError("Team evaluation uses actual tools, not mocked_tools")
    connected = case.get("purpose") == "connected_acceptance"
    replay = case.get("purpose") == "captured_replay"
    if replay:
        from portfolio_management.adapters.market.captured import CapturedMarketProvider

        provider = deps.market_provider
        if provider is None and config.market_capture_manifest_path:
            provider = CapturedMarketProvider(config.market_capture_manifest_path)
        if type(provider) is not CapturedMarketProvider:
            raise ValueError("Replay requires an explicitly selected finalized capture provider")
        description = provider.describe()
        for field in ("capture_id", "index_sha256", "collection_sha256"):
            if description.get(field) != case.get("capture", {}).get(field):
                raise ValueError("Selected finalized capture differs from the replay case")
        deps = replace(deps, market_provider=provider)
    if connected:
        market: dict[str, Any] = getattr(deps.market_provider, "describe", lambda: {})()
        research = getattr(deps, "research_backend", None)
        research_description = research.describe() if research is not None else {}
        if (
            mode != "team"
            or market.get("mode") != "connected"
            or research_description.get("mode") != "connected"
        ):
            raise ValueError(
                "Connected acceptance requires connected market and public research backends"
            )
        if case.get("fixtures") or case.get("mocked_tools") or case.get("role_mocks"):
            raise ValueError("Connected acceptance cannot substitute replay or mocked evidence")
    if mode == "team" and deps.market_provider is not None and not connected and not replay:
        raise ValueError(
            "Team evaluation requires its case-bound controlled fixture; "
            "provider overrides are forbidden"
        )
    if case.get("fictional_issuer") and mode != "scripted":
        raise ValueError(
            "Fictional issuer scenarios are retired from actual-model role/team acceptance"
        )
    fixture_clock = case.get("fixtures", {}).get("clock")
    analysis_at = datetime.fromisoformat(fixture_clock) if fixture_clock else deps.clock()
    if analysis_at.utcoffset() is None:
        raise ValueError("Evaluation analysis clock must be timezone-aware")
    if fixture_clock:
        deps = replace(deps, clock=lambda: analysis_at)
    request = TeamRequest.model_validate(
        {
            "question": case["question"],
            "mode": case.get("mode", "education"),
            "portfolio": case.get("portfolio"),
        }
    )
    measurement_started = datetime.now(UTC)
    started = time.perf_counter()
    if mode == "role":
        result, paths = await _run_role(case, request, config, deps)
    else:
        selected = replace(deps, evaluation_mode=mode)  # type: ignore[arg-type]
        if mode == "team" and not connected and not replay:
            selected = replace(
                selected, market_provider=CaseMarketProvider(case.get("fixtures", {}))
            )
        team_result = await run_team(request, config=config, deps=selected)
        result, paths = team_result.model_dump(mode="json"), team_result.artifacts
    elapsed = time.perf_counter() - started
    measurement_completed = datetime.now(UTC)
    manifest, trace, evidence = _read_artifacts(paths)
    findings = evaluate_findings(case, result, trace, evidence)
    generations = [row for row in trace if row.get("event") == "model_generation_started"]
    tools = [
        row
        for row in trace
        if row.get("event") == "tool_call_started"
        and row.get("name") not in {"TeamResult", "ResearchResult", "ExpertResult"}
    ]
    usages = [row.get("usage") for row in trace if row.get("event") == "model_generation_finished"]
    usage = None
    if usages and all(
        isinstance(item, dict)
        and all(key in item for key in ("inputTokens", "outputTokens", "totalTokens"))
        for item in usages
    ):
        usage = {
            target: sum(item[source] for item in usages)
            for source, target in (
                ("inputTokens", "input_tokens"),
                ("outputTokens", "output_tokens"),
                ("totalTokens", "total_tokens"),
            )
        }
    prompts = manifest.get(
        "prompt_hashes",
        {"team_lead": manifest.get("prompt_hash"), **manifest.get("shared_hashes", {})},
    )
    if manifest.get("role_prompts"):
        prompts = manifest["role_prompts"]
    completion = result.get(
        "completion_reason",
        "complete"
        if result.get("outcome") == "success" or "assessment" in result
        else result.get("outcome", "failed"),
    )
    summary = {
        "case_id": case["case_id"],
        "repetition": case.get("repetition", 1),
        "evaluation_mode": mode,
        "role": case.get("role", "team_lead"),
        "live_acceptance": False,
        "inference_kind": "scripted"
        if mode == "scripted"
        else "injected_sdk_transport"
        if deps.model_factory
        else "configured_model",
        "run_id": result["run_id"],
        "completion_reason": completion,
        "artifacts": paths,
        "final_response": result.get("report_markdown")
        or result.get("summary")
        or result.get("assessment")
        or "",
        "result": result,
        "evidence": evidence,
        "trace": trace,
        "findings": findings,
        "outcome_categories": categorize_outcomes(result, findings, trace, evidence),
        "purpose": case.get("purpose", "mechanics"),
        "evidence_mode": "connected"
        if connected
        else "captured_replay"
        if replay
        else "controlled_testing",
        "market_provider": manifest.get("market_provider"),
        "research_backend": manifest.get("research_backend"),
        "case_hash": content_hash(
            {key: value for key, value in case.items() if key not in {"repetition", "hardware_id"}}
        ),
        "evidence_hash": content_hash(
            {"provider": manifest.get("market_provider"), "records": evidence}
        )
        if connected or replay
        else content_hash(case.get("fixtures", {})),
        "evidence_as_of": case.get("fixtures", {}).get("clock"),
        "analysis_as_of": analysis_at.isoformat(),
        "model_id": config.model.model_id,
        "model_settings": config.model.model_dump(
            exclude={"script", "endpoint", "api_key_env", "allow_inference_network"}
        ),
        "prompt_hashes": prompts,
        "knowledge_hashes": manifest.get("knowledge_hashes", {}),
        "tool_specs_hash": manifest.get("tool_specs_hash"),
        "limits": {
            key: value
            for key, value in config.model_dump(mode="json").items()
            if key not in {"model", "prompts", "output_dir", "knowledge_manifest_path"}
        },
        "started_at": measurement_started.isoformat(),
        "completed_at": measurement_completed.isoformat(),
        "analysis_started_at": manifest.get("started_at"),
        "analysis_completed_at": manifest.get("completed_at"),
        "hardware_id": case.get("hardware_id"),
        "metrics": {
            "elapsed_seconds": elapsed,
            "model_calls": len(generations),
            "tool_calls": len(tools),
            "usage": usage,
        },
    }
    _write(Path(paths["manifest"]).parent / "evaluation-summary.json", summary)
    return summary


def write_review_packet(run: dict[str, Any], output: Path) -> Path:
    """Save observable evidence and a human-authored assessment placeholder."""
    run = copy.deepcopy(run)
    run.setdefault(
        "outcome_categories",
        categorize_outcomes(
            run.get("result", run),
            run.get("findings", []),
            run.get("trace", []),
            run.get("evidence", []),
        ),
    )
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest = content_hash(run)
    packet_path, assessment_path = output / "review-packet.json", output / "assessment.json"
    for existing in (packet_path, assessment_path):
        if existing.exists() and json.loads(existing.read_text()).get("packet_hash") != digest:
            raise ValueError("Review directory is bound to a different packet")
    _write(packet_path, {"packet_hash": digest, "run": run})
    if not assessment_path.exists():
        _write(
            assessment_path,
            {
                "packet_hash": digest,
                "reviewer": None,
                "verdict": None,
                "rationale": None,
                "evidence_refs": [],
                "suggested_changes": [],
            },
        )
    (output / "review.md").write_text(
        f"# Review packet\n\nPacket SHA256: {digest}\n\n"
        + str(run.get("final_response") or "No final output.")
        + "\n\n## Deterministic findings\n\n```json\n"
        + json.dumps(run.get("findings", []), indent=2, ensure_ascii=False)
        + "\n```\n\n## Outcome categories\n\n```json\n"
        + json.dumps(run["outcome_categories"], indent=2, ensure_ascii=False)
        + "\n```\n\nVisible trace and evidence: review-packet.json. "
        "Review with evals/thin/rubric.md; record judgment in assessment.json.\n",
        encoding="utf-8",
    )
    return output


def mock_tools_from_specs(
    specs: dict[str, Any], fixtures: dict[str, Any], trace: TraceSink
) -> dict[str, Any]:
    """Exact advertised schemas, argument-sensitive synthetic replies, no production handlers.

    Separate from legacy queue mocks: no prescribed call order or investment grader.
    All unmatched arguments return explicit mock coverage limitations.
    """
    if set(fixtures) - set(specs):
        raise ValueError("Fixture names are not advertised tools")

    def matches(rule: dict[str, Any], arguments: dict[str, Any]) -> bool:
        text = json.dumps(arguments, sort_keys=True, ensure_ascii=False).casefold()
        return all(str(token).casefold() in text for token in rule.get("contains_all", []))

    def construct(name: str, spec: dict[str, Any]) -> Any:
        fixture = copy.deepcopy(fixtures.get(name, {}))
        calls = 0
        from jsonschema import Draft202012Validator

        validator = Draft202012Validator(spec["inputSchema"]["json"])

        def select(arguments: dict[str, Any], routes: list[Any], default: Any = None) -> Any:
            for route in routes:
                if matches(route, arguments):
                    return copy.deepcopy(route["response"])
            if default is not None:
                return copy.deepcopy(default)
            trace.emit("mock_coverage", name=name, category="unconfigured_arguments")
            return {"status": "unavailable", "reason": "No fixture covers this request"}

        async def controlled(tool_context: ToolContext | None = None, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            # Validate raw advertised arguments, not SDK-injected Python defaults.
            # Explicit invalid nulls and required-field checks remain unchanged.
            supplied = tool_context.tool_use["input"] if tool_context is not None else kwargs
            if not validator.is_valid(supplied):
                trace.emit("mock_coverage", name=name, category="schema_invalid_arguments")
                raise ValueError("Tool arguments do not match frozen production schema")
            if fixture.get("coverage_limited"):
                trace.emit("mock_coverage", name=name, category="configured_mock_limitation")
            if name == "fact_check" and calls > 3:
                value = {"status": "limit_reached", "feedback": "", "error": "author_call_limit"}
                trace.emit("mock_tool_result", name=name, arguments=supplied, result=value)
                return value
            if "reports_by_topic" in fixture:
                reports = []
                for job in kwargs.get("jobs", []):
                    value = select(job, fixture["reports_by_topic"], fixture.get("default_report"))
                    if value.get("status") == "unavailable":
                        value = {
                            "outcome": "partial",
                            "summary": value["reason"],
                            "evidence_refs": [],
                            "error": "mock_coverage",
                        }
                    reports.append({**value, "job_id": job["job_id"]})
                value = (
                    reports
                    if name == "research_batch"
                    else {
                        "status": "completed",
                        "reports": reports,
                        "request_mapping": {r["job_id"]: r["job_id"] for r in reports},
                    }
                )
            else:
                value = select(kwargs, fixture.get("routes", []), fixture.get("default"))
            trace.emit("mock_tool_result", name=name, arguments=supplied, result=value)
            return value

        schema = spec["inputSchema"]["json"]
        required = set(schema.get("required", []))
        controlled.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
            [
                inspect.Parameter(
                    field,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=Any,
                    default=inspect.Parameter.empty if field in required else prop.get("default"),
                )
                for field, prop in schema.get("properties", {}).items()
            ]
            + [
                inspect.Parameter(
                    "tool_context",
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=ToolContext,
                    default=None,
                )
            ]
        )
        return tool(
            name=name,
            description=spec["description"],
            inputSchema=copy.deepcopy(spec["inputSchema"]),
            context=True,
        )(controlled)

    return {name: construct(name, spec) for name, spec in specs.items()}
