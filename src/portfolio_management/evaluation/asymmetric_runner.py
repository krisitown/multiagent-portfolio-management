"""Run one bounded public specialist: python -m portfolio_management.evaluation.asymmetric_runner."""

import argparse
import asyncio
import hashlib
import json
import os
import signal
import time
import tomllib
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from strands.models import Model

from portfolio_management.adapters.market.captured import CapturedMarketProvider
from portfolio_management.adapters.market.spark import SparkMarketProvider
from portfolio_management.adapters.research import HermesResearchBackend
from portfolio_management.asymmetric import AsymmetricConfig, build_asymmetric, utc_now
from portfolio_management.model_observer import CompletionTelemetry
from portfolio_management.models import budget_failure, build_model
from portfolio_management.types import RunBudget, TraceSink
from portfolio_management.vault import EvidenceStore


def _write(path: Path, value: Any) -> None:
    payload = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    _write_text(path, payload)


def _write_text(path: Path, value: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(value)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def load_config(path: Path, *, from_team_config: bool = False) -> AsymmetricConfig:
    """Optionally project only explicit model/budget/public-provider settings."""
    path = path.expanduser().resolve()
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw) if path.suffix == ".json" else tomllib.loads(raw)
    if from_team_config:
        fields = set(AsymmetricConfig.model_fields)
        payload = {key: value for key, value in payload.items() if key in fields}
    config = AsymmetricConfig.model_validate(payload)
    updates: dict[str, Any] = {}
    if config.market_capture_manifest_path is not None:
        selected = config.market_capture_manifest_path.expanduser()
        updates["market_capture_manifest_path"] = (
            selected if selected.is_absolute() else path.parent / selected
        ).resolve()
    if config.market_backend is not None:
        paths = {}
        for name in ("identity_file", "capture_root", "helper_path"):
            selected = getattr(config.market_backend, name)
            if selected is not None:
                selected = selected.expanduser()
                paths[name] = (
                    selected if selected.is_absolute() else path.parent / selected
                ).resolve()
        updates["market_backend"] = replace(config.market_backend, **paths)
    return config.model_copy(update=updates)


async def run_asymmetric(
    *,
    task: str,
    prompt: str,
    config: AsymmetricConfig,
    output_dir: Path,
    model: Model | None = None,
    market_provider: Any = None,
    research_backend: Any = None,
    clock: Callable[[], datetime] = utc_now,
) -> dict[str, Any]:
    """Persist observable public evidence on completion/failure/cancel; no live defaults."""
    if not task.strip() or len(task.encode()) > 1_000_000:
        raise ValueError("Task must contain text and fit the 1MB input bound")
    if not prompt.strip() or len(prompt.encode()) > 2_000_000:
        raise ValueError("Prompt must contain text and fit the 2MB input bound")
    started = clock()
    if started.utcoffset() is None:
        raise ValueError("Clock must be timezone-aware")
    output_dir = output_dir.expanduser().absolute()
    output_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(output_dir, 0o700)
    _write_text(output_dir / "system-prompt.md", prompt)
    _write_text(output_dir / "task.md", task)
    trace = TraceSink(output_dir / "trace.jsonl", clock)
    budget = RunBudget(
        config.max_generations,
        0,
        started + timedelta(seconds=config.deadline_seconds),
        asyncio.Semaphore(1),
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "role": "asymmetric_opportunities",
        "run_id": output_dir.name,
        "started_at": started.isoformat(),
        "status": "running",
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "task_sha256": hashlib.sha256(task.encode()).hexdigest(),
        "model": config.model.model_dump(mode="json", exclude={"script", "api_key_env"}),
        "inference_kind": "injected_sdk_transport" if model is not None else config.model.provider,
        "limits": {
            "max_generations": config.max_generations,
            "deadline_seconds": config.deadline_seconds,
        },
        "scope": "standalone public advisory research; no private account tools",
        "tool_specs": [],
        "generations": 0,
        "usage": None,
        "runtime_files_sha256": {
            str(relative): hashlib.sha256(
                (Path(__file__).resolve().parents[1] / relative).read_bytes()
            ).hexdigest()
            for relative in (
                Path("asymmetric.py"),
                Path("evaluation/asymmetric_runner.py"),
                Path("tools/option_scenarios.py"),
                Path("domain/option_pricing.py"),
                Path("adapters/market/spark.py"),
                Path("adapters/market/collector.py"),
                Path("tools/research.py"),
                Path("models.py"),
                Path("model_observer.py"),
            )
        },
    }
    _write(output_dir / "manifest.json", manifest)
    client = None
    hooks = None
    report, status, error_category = "", "failed", None
    measured_started = time.perf_counter()
    try:
        async with asyncio.timeout(config.deadline_seconds):
            if market_provider is None:
                market_provider = (
                    SparkMarketProvider(config.market_backend)
                    if config.market_backend is not None
                    else CapturedMarketProvider(config.market_capture_manifest_path)
                    if config.market_capture_manifest_path is not None
                    else None
                )
            if research_backend is None and config.research_backend is not None:
                research_backend = HermesResearchBackend(config.research_backend)
            telemetry = CompletionTelemetry() if model is None else None
            if model is None:
                model, client = build_model(config.model, telemetry=telemetry)
            if not isinstance(model, Model):
                raise ValueError("Standalone role requires an actual Strands SDK Model")
            agent, hooks = build_asymmetric(
                model=model,
                prompt=prompt,
                store=EvidenceStore(output_dir / "records"),
                budget=budget,
                trace=trace,
                market_provider=market_provider,
                research_backend=research_backend,
                telemetry=telemetry,
            )
            specs = agent.tool_registry.get_all_tool_specs()
            manifest.update(tool_specs=specs, tool_specs_sha256=_hash(specs))
            effective = agent.system_prompt
            assert isinstance(effective, str)
            _write_text(output_dir / "effective-system-prompt.md", effective)
            manifest["effective_prompt_sha256"] = hashlib.sha256(effective.encode()).hexdigest()
            manifest["market_provider"] = (
                market_provider.describe() if hasattr(market_provider, "describe") else None
            )
            manifest["research_backend"] = (
                research_backend.describe() if research_backend is not None else None
            )
            _write(output_dir / "manifest.json", manifest)
            response = await agent.invoke_async(task)
            report = "".join(
                block["text"]
                for block in response.message.get("content", [])
                if isinstance(block.get("text"), str)
            )
            status = (
                "complete"
                if report.strip() and response.stop_reason in {"end_turn", "stop_sequence"}
                else "incomplete"
            )
            manifest["stop_reason"] = response.stop_reason
    except asyncio.CancelledError:
        status = "cancelled"
        trace.emit("run_cancelled")
    except TimeoutError:
        status = "deadline"
        trace.emit("run_deadline")
    except Exception as exc:
        boundary = budget_failure(exc)
        status = (
            "generation_limit" if boundary == "generation" else "deadline" if boundary else "failed"
        )
        error_category = type(exc).__name__
        trace.emit("run_failed", category=error_category, budget_boundary=boundary)
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception as exc:
                trace.emit("client_cleanup_failed", category=type(exc).__name__)
        partial = hooks.last_text if hooks is not None else ""
        usage = None
        if hooks is not None:
            complete_usage = (
                len(hooks.usages) == budget.generations
                and hooks.usages
                and all(
                    isinstance(row, dict)
                    and all(
                        type(row.get(key)) is int
                        for key in ("inputTokens", "outputTokens", "totalTokens")
                    )
                    for row in hooks.usages
                )
            )
            if complete_usage:
                usage = {
                    key: sum(row[key] for row in hooks.usages if row is not None)
                    for key in ("inputTokens", "outputTokens", "totalTokens")
                }
            _write(output_dir / "assistant-messages.json", hooks.ordinary_messages)
        manifest.update(
            status=status,
            completed_at=clock().isoformat(),
            elapsed_seconds=time.perf_counter() - measured_started,
            generations=budget.generations,
            usage=usage,
            error_category=error_category,
        )
        result = {
            "status": status,
            "final_output": report,
            "partial_output": partial if status != "complete" else "",
            "error_category": error_category,
            "output_dir": str(output_dir),
        }
        _write_text(output_dir / "report.md", report or partial)
        _write(output_dir / "result.json", result)
        _write(output_dir / "manifest.json", manifest)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--from-team-config",
        action="store_true",
        help="Project only model, run budget and public provider settings",
    )
    parser.add_argument("--task-file", type=Path, required=True)
    parser.add_argument(
        "--prompt-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "prompts/asymmetric_opportunities.md",
        help="Exact prompt override; defaults to the packaged specialist prompt",
    )
    parser.add_argument("--output", type=Path, required=True, help="New run directory")
    parser.add_argument("--max-generations", type=int, help="Override finite generation ceiling")
    parser.add_argument(
        "--deadline-seconds", type=float, help="Override finite wall-clock deadline"
    )
    args = parser.parse_args(argv)
    config = load_config(args.config, from_team_config=args.from_team_config)
    overrides = {
        name: value
        for name in ("max_generations", "deadline_seconds")
        if (value := getattr(args, name)) is not None
    }
    config = AsymmetricConfig.model_validate({**config.model_dump(), **overrides})

    async def execute() -> dict[str, Any]:
        current = asyncio.current_task()
        loop = asyncio.get_running_loop()
        assert current is not None
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, current.cancel)
        return await run_asymmetric(
            task=args.task_file.read_text(encoding="utf-8"),
            prompt=args.prompt_file.read_text(encoding="utf-8"),
            config=config,
            output_dir=args.output,
        )

    result = asyncio.run(execute())
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
