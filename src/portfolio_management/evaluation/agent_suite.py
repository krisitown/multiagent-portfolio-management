"""Isolated current SDK roles with synthetic tools; ordinary evidence, no semantic autograder."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from strands import Agent
from strands.agent.conversation_manager import NullConversationManager
from strands.models import Model

from portfolio_management.agents import build_analyst, build_explorer, build_team_lead, build_wheel_expert
from portfolio_management.config import TeamConfig
from portfolio_management.evaluation.thin_runner import _write, content_hash, mock_tools_from_specs
from portfolio_management.fact_check_runtime import SCOPE, _OrdinaryReply
from portfolio_management.models import RunHooks, build_model
from portfolio_management.prompt_loader import load_caller_amendments, load_prompt
from portfolio_management.types import RoleContext, RunBudget, TeamRequest, TraceSink

ROLES = ("explorer", "analyst", "wheel_expert", "team_lead", "fact_checker", "hermes_presentation")


def validate_suite(cases: list[dict[str, Any]]) -> None:
    seen = set()
    for case in cases:
        if any(k in case for k in ("expected_facts", "gold", "rubric", "grading")):
            raise ValueError("Private grading must stay outside agent-visible cases")
        key = case.get("case_id", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", key):
            raise ValueError("Invalid logical case_id")
        if key in seen:
            raise ValueError("Duplicate case_id")
        seen.add(key)
        if case.get("role") not in ROLES or case.get("simulation") is not True:
            raise ValueError("Each role case must explicitly declare simulation=true")
        if not case.get("question"):
            raise ValueError("Case question is required")
    if not 1 <= len(cases) <= 24:
        raise ValueError("Select between1 and24 cases")


async def run_agent_case(
    case: dict[str, Any],
    config: TeamConfig,
    tool_specs: dict[str, Any],
    output: Path,
    *,
    model: Model | None = None,
    system_prompts: dict[str, str] | None = None,
) -> dict[str, Any]:
    validate_suite([case])
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    start = time.monotonic()
    role = case["role"]

    def clock():
        return datetime.fromisoformat(case.get("analysis_time", "2026-11-04T12:00:00+00:00"))

    class EvalTrace(TraceSink):
        def emit(self, event, **values):
            super().emit(event, wall_at=datetime.now(UTC).isoformat(), **values)

    trace = EvalTrace(output / "trace.jsonl", clock)
    budget = RunBudget(
        config.max_generations,
        config.max_expert_consultations,
        clock() + timedelta(seconds=config.deadline_seconds),
        asyncio.Semaphore(1),
    )
    record = {
        "case_id": case["case_id"],
        "role": role,
        "simulation": True,
        "real_Hermes_process": False,
        "attempted": False,
        "status": "started",
        "started_at": datetime.now(UTC).isoformat(),
        "grading": None,
        "dependency_feedback": "All checker/delegation/research results are mocked",
        "trace_clock": "scenario_time",
        "analysis_time": clock().isoformat(),
        "case_sha256": content_hash(case),
        "ordinary_output": "",
        "tool_specs": {},
    }
    _write(output / "result.json", record)
    client = None
    ordinary = []

    def capture(text, reason, tool_use):
        if text:
            ordinary.append(text)

    try:
        if model is None:
            selected = config.model
            if role == "explorer" and config.explorer_temperature is not None:
                selected = selected.model_copy(update={"temperature": config.explorer_temperature})
            model, client = build_model(selected)
        if role in {"fact_checker", "hermes_presentation"}:
            if system_prompts and role in system_prompts:
                prompt = system_prompts[role]
            elif role == "fact_checker" and "system_prompt" not in case:
                prompt = load_prompt("fact_checker", config.fact_check.prompts).content + SCOPE
            else:
                prompt = case["system_prompt"]
            hooks = RunHooks(budget, trace, "fact_checker", case["case_id"])
            hooks.role = role
            agent = Agent(
                model=model,
                system_prompt=prompt,
                tools=[],
                hooks=[hooks, _OrdinaryReply(capture)],
                callback_handler=None,
                retry_strategy=None,
                conversation_manager=NullConversationManager(),
            )
        else:
            prompt = load_prompt(role, config.prompts)
            tools = mock_tools_from_specs(
                tool_specs.get(role, {}), case.get("tool_fixtures", {}), trace
            )
            request = TeamRequest(
                question=case["question"],
                mode=case.get("mode", "education"),
                portfolio_text=case.get("portfolio_text"),
                owner_context=case.get("owner_context"),
            )
            context = RoleContext(
                request,
                config,
                model,
                prompt,
                tools,
                budget,
                trace,
                output,
                invocation_id=case["case_id"],
                monotonic_deadline=start + config.deadline_seconds,
                invocation_deadline=start + config.deadline_seconds,
            )
            if config.fact_check.caller_amendments_path:
                prompts = {r: load_prompt(r, config.prompts) for r in ["analyst", "wheel_expert"]}
                amendments, _ = load_caller_amendments(
                    config.fact_check.caller_amendments_path, prompts
                )
                context.fact_check_amendment = amendments.get(role, "")
            agent = {
                "analyst": build_analyst,
                "wheel_expert": build_wheel_expert,
                "explorer": build_explorer,
                "team_lead": build_team_lead,
            }[role](context)
            agent.hooks.add_hook(_OrdinaryReply(capture))
        specs = agent.tool_registry.get_all_tool_specs()
        record["tool_specs"] = {s["name"]: s for s in specs}
        record["tool_specs_sha256"] = content_hash(record["tool_specs"])
        (output / "system-prompt.txt").write_text(agent.system_prompt)
        record["system_prompt_sha256"] = content_hash(agent.system_prompt)
        _write(output / "case.json", case)
        async with asyncio.timeout(config.deadline_seconds):
            response = await agent.invoke_async(case["question"])
        text = "".join(
            b["text"] for b in response.message.get("content", []) if isinstance(b.get("text"), str)
        )
        record.update(
            status="completed" if text.strip() else "empty_reply",
            ordinary_output=text,
            stop_reason=response.stop_reason,
        )
    except Exception as exc:
        record.update(
            status="timeout" if isinstance(exc, TimeoutError) else "error",
            error_category=type(exc).__name__,
            ordinary_output="\n".join(ordinary),
        )
    finally:
        if client:
            await client.close()
    rows = (
        [json.loads(s) for s in (output / "trace.jsonl").read_text().splitlines()]
        if (output / "trace.jsonl").exists()
        else []
    )
    generations = [r for r in rows if r.get("event") == "model_generation_finished"]
    usage = [r["usage"] for r in generations if isinstance(r.get("usage"), dict) and r["usage"]]
    record["attempted"] = any(r.get("event") == "model_generation_started" for r in rows)
    if not record["attempted"]:
        record["status"] = "harness_error"
    record.update(
        completed_at=datetime.now(UTC).isoformat(),
        latency_seconds=time.monotonic() - start,
        model_generations=budget.generations,
        usage={
            k: sum(u.get(k, 0) for u in usage)
            for k in ("inputTokens", "outputTokens", "totalTokens")
        }
        if usage
        else None,
        model_events=generations,
        mock_coverage_events=[r for r in rows if r.get("event") == "mock_coverage"],
        tool_trace=[r for r in rows if r.get("event") == "mock_tool_result"],
    )
    _write(output / "result.json", record)
    (output / "ordinary-output.md").write_text(record["ordinary_output"])
    return record


def load_suite(root: Path) -> tuple[list[Any], TeamConfig, dict[str, Any], dict[str, str]]:
    root = root.resolve()
    # Validate the immutable data bundle before constructing any SDK transport.
    manifest_path = root / "bundle-manifest.json"
    if manifest_path.exists():
        for item in json.loads(manifest_path.read_text())["files"]:
            path = (root / item["path"]).resolve()
            if (
                not path.is_relative_to(root)
                or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]
            ):
                raise ValueError("Frozen eval bundle changed: " + item["path"])
    cases = json.loads((root / "cases.json").read_text())
    validate_suite(cases)
    raw = json.loads((root / "config.json").read_text())
    for section in [raw["prompts"], raw["fact_check"]["prompts"]]:
        section["manifest_path"] = str(root / section["manifest_path"])
    raw["knowledge_manifest_path"] = str(root / raw["knowledge_manifest_path"])
    raw["fact_check"]["caller_amendments_path"] = str(
        root / raw["fact_check"]["caller_amendments_path"]
    )
    config = TeamConfig.model_validate(raw)
    specs = json.loads((root / "tool-specs.json").read_text())
    for role in ROLES[:4]:
        load_prompt(role, config.prompts)
    checker = load_prompt("fact_checker", config.fact_check.prompts).content + SCOPE
    # Knowledge remains a read-only frozen fixture, never a live lookup.
    knowledge_path = Path(config.knowledge_manifest_path)
    from portfolio_management.knowledge_loader import load_knowledge, read_module

    knowledge = load_knowledge(knowledge_path)
    routes = [
        {
            "contains_all": [m.module_id],
            "response": {
                "status": "success",
                "data": {
                    "module": m.model_dump(mode="json"),
                    "content": read_module(knowledge_path, m),
                },
            },
        }
        for m in knowledge.modules
    ]
    index = [{"module_id": m.module_id, "title": m.title} for m in knowledge.modules]
    for case in cases:
        if case["role"] in {"analyst", "wheel_expert"}:
            case["tool_fixtures"]["knowledge_read"] = {
                "routes": routes,
                "default": {
                    "status": "unavailable",
                    "reason": "Module not present in the selected authored index",
                },
            }
            case["question"] += (
                "\nAuthored knowledge index (read with knowledge_read):\n" + json.dumps(index)
            )
        if set(case.get("tool_fixtures", {})) - set(specs.get(case["role"], {})):
            raise ValueError("Case has tool not in current advertised schema")
    editorial = (root / "prompts/hermes-editorial-instructions.txt").read_text()
    hermes = (
        editorial
        + "\n\nEvaluation transport adapter: This is a zero-tool presentation-role simulation "
        "with the same eval model, not a real Hermes process. The user packet supplies the "
        "unchanged native report and already prepared brief-context.json; do not run commands. "
        "Return the separate delivery-brief.json contents as your ordinary final response. "
        "No files or email are actually written or sent. Mock delivery acknowledgement is "
        "data, not proof of delivery."
    )
    return cases, config, specs, {"fact_checker": checker, "hermes_presentation": hermes}


async def run_batch(
    cases: list[dict[str, Any]],
    config: TeamConfig,
    specs: dict[str, Any],
    output: Path,
    *,
    concurrency: int = 2,
    stop_before: datetime | None = None,
    dry_run: bool = False,
    system_prompts: dict[str, str] | None = None,
) -> dict[str, Any]:
    validate_suite(cases)
    if not 1 <= concurrency <= 2:
        raise ValueError("Concurrency must be1 or2")
    if stop_before is not None and stop_before.utcoffset() is None:
        raise ValueError("stop-before must include timezone")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    records = [
        {
            "case_id": c["case_id"],
            "role": c["role"],
            "attempted": False,
            "status": "dry_run" if dry_run else "not_attempted",
        }
        for c in cases
    ]
    for r in records:
        _write(output / (r["case_id"] + ".json"), r)
    cursor = 0
    dispatch_lock = asyncio.Lock()

    async def worker():
        nonlocal cursor
        while True:
            async with dispatch_lock:
                if cursor == len(cases):
                    return
                i = cursor
                cursor += 1
                if (
                    stop_before is not None
                    and datetime.now(UTC) + timedelta(seconds=config.deadline_seconds + 60)
                    > stop_before
                ):
                    records[i]["status"] = "deferred_schedule"
                    _write(output / (cases[i]["case_id"] + ".json"), records[i])
                    continue
            records[i] = await run_agent_case(
                cases[i], config, specs, output / cases[i]["case_id"], system_prompts=system_prompts
            )
            _write(output / (cases[i]["case_id"] + ".json"), records[i])
            _write(output / "progress.json", {"cases": records})

    if not dry_run:
        await asyncio.gather(*(worker() for _ in range(concurrency)))
    summary = {
        "simulation": True,
        "selected": len(cases),
        "attempted": sum(r["attempted"] for r in records),
        "deferred": sum(r["status"] == "deferred_schedule" for r in records),
        "cases": records,
        "semantic_grading": (
            "Pending independent human/root ordinary-output review; no automatic financial grading"
        ),
        "real_Hermes_process": False,
        "real_email": False,
        "concurrency": concurrency,
        "case_wall_deadline_seconds": config.deadline_seconds,
        "automatic_retries": 0,
        "stop_before": stop_before.isoformat() if stop_before else None,
    }
    _write(output / "summary.json", summary)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path("evals/agent_v1"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--role", choices=ROLES, action="append")
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--stop-before", type=datetime.fromisoformat)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args(argv)
    cases, config, specs, prompts = load_suite(args.suite)
    selected = [
        c
        for c in cases
        if (not args.role or c["role"] in args.role)
        and (not args.case_ids or c["case_id"] in args.case_ids)
    ]
    validate_suite(selected)
    if args.validate:
        print(
            json.dumps(
                {
                    "status": "offline_validation_passed",
                    "cases": len(selected),
                    "roles": sorted({c["role"] for c in selected}),
                    "network_requests": 0,
                }
            )
        )
        return 0
    if args.output is None:
        parser.error("--output is required for a fresh run directory")
    os.umask(0o077)
    summary = asyncio.run(
        run_batch(
            selected,
            config,
            specs,
            args.output,
            concurrency=args.concurrency,
            stop_before=args.stop_before,
            dry_run=args.dry_run,
            system_prompts=prompts,
        )
    )
    print(
        json.dumps(
            {
                "selected": summary["selected"],
                "attempted": summary["attempted"],
                "deferred": summary["deferred"],
                "output": str(args.output),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
