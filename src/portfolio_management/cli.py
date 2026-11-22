"""Foreground question and explicit single-case evaluation commands."""

import argparse
import asyncio
import json
import sys
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any

from portfolio_management.adapters.market.captured import CapturedMarketProvider
from portfolio_management.adapters.market.spark import SparkMarketProvider
from portfolio_management.adapters.research import HermesResearchBackend
from portfolio_management.config import TeamConfig
from portfolio_management.evaluation.thin_runner import run_case
from portfolio_management.team import run_team
from portfolio_management.types import TeamDependencies, TeamRequest


def load_team_config(path: Path) -> TeamConfig:
    """Resolve selected files relative to their config rather than the caller's cwd."""
    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw) if path.suffix == ".json" else tomllib.loads(raw)
    config = TeamConfig.model_validate(payload)
    updates: dict[str, Any] = {}
    if not config.output_dir.is_absolute():
        updates["output_dir"] = (path.parent / config.output_dir).resolve()
    if (
        config.knowledge_manifest_path is not None
        and not config.knowledge_manifest_path.is_absolute()
    ):
        updates["knowledge_manifest_path"] = (
            path.parent / config.knowledge_manifest_path
        ).resolve()
    if config.prompts.manifest_path is not None and not config.prompts.manifest_path.is_absolute():
        updates["prompts"] = config.prompts.model_copy(
            update={
                "manifest_path": (path.parent / config.prompts.manifest_path).resolve(),
            }
        )
    checker_path = config.fact_check.prompts.manifest_path
    if checker_path is not None and not checker_path.is_absolute():
        updates["fact_check"] = config.fact_check.model_copy(
            update={
                "prompts": config.fact_check.prompts.model_copy(
                    update={"manifest_path": (path.parent / checker_path).resolve()}
                )
            }
        )
    amendment_path = config.fact_check.caller_amendments_path
    if amendment_path is not None and not amendment_path.is_absolute():
        selected = updates.get("fact_check", config.fact_check)
        updates["fact_check"] = selected.model_copy(
            update={"caller_amendments_path": (path.parent / amendment_path).resolve()}
        )
    tokenizer_path = config.fact_check.tokenizer_path
    if tokenizer_path is not None and not tokenizer_path.is_absolute():
        selected = updates.get("fact_check", config.fact_check)
        updates["fact_check"] = selected.model_copy(
            update={"tokenizer_path": (path.parent / tokenizer_path).resolve()}
        )
    if (
        config.market_capture_manifest_path is not None
        and not config.market_capture_manifest_path.is_absolute()
    ):
        updates["market_capture_manifest_path"] = (
            path.parent / config.market_capture_manifest_path
        ).resolve()
    if config.market_backend is not None:
        local_paths: dict[str, Any] = {}
        for field in ("identity_file", "capture_root", "helper_path"):
            value = getattr(config.market_backend, field)
            if value is not None:
                expanded = value.expanduser()
                local_paths[field] = (
                    expanded if expanded.is_absolute() else (path.parent / expanded).resolve()
                )
        updates["market_backend"] = replace(config.market_backend, **local_paths)
    return config.model_copy(update=updates)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="portfolio-management")
    commands = parser.add_subparsers(dest="command", required=True)
    telemetry = commands.add_parser("telemetry", help="Read-only private run viewer")
    telemetry.add_argument("--run-dir", type=Path, required=True)
    telemetry.add_argument("--port", type=int, default=8765)
    telemetry.add_argument("--metrics-url")
    feedback = commands.add_parser(
        "feedback", help="Preserve selected original owner feedback and discuss/maintain it"
    )
    ask = commands.add_parser("ask")
    ask.add_argument("--question", required=True)
    ask.add_argument(
        "--mode",
        required=True,
        choices=[
            "discovery",
            "portfolio",
            "focused",
            "education",
            "combined",
        ],
    )
    portfolio_input = ask.add_mutually_exclusive_group()
    portfolio_input.add_argument("--portfolio", type=Path)
    portfolio_input.add_argument("--portfolio-text", type=Path)
    ask.add_argument("--owner-context", type=Path)
    ask.add_argument("--parent-review", type=Path)
    ask.add_argument(
        "--review-kind", choices=["full", "explanation", "targeted", "refresh"], default="full"
    )
    evaluate = commands.add_parser("eval")
    evaluate.add_argument("--case", type=Path, required=True)
    evaluate.add_argument("--mode", required=True, choices=["scripted", "role", "team"])
    for command in (ask, evaluate, feedback):
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "telemetry":
            from portfolio_management.telemetry.server import serve

            return serve(args.run_dir, args.port, args.metrics_url)
        config = load_team_config(args.config).model_copy(update={"output_dir": args.output})
        if args.command == "feedback":
            from portfolio_management.owner_feedback import run_feedback

            outcome = asyncio.run(run_feedback(json.load(sys.stdin), config=config))
            print(json.dumps(outcome, ensure_ascii=False))
            return 0 if outcome.get("source_preserved") else 1
        explanation_only = args.command == "ask" and args.review_kind == "explanation"
        deps = TeamDependencies(
            evaluation_mode="team" if config.model.provider == "openai" else "scripted",
            market_provider=(
                None
                if explanation_only
                else SparkMarketProvider(config.market_backend)
                if config.market_backend is not None
                else CapturedMarketProvider(config.market_capture_manifest_path)
                if config.market_capture_manifest_path is not None
                else None
            ),
            research_backend=(
                None
                if explanation_only
                else HermesResearchBackend(config.research_backend)
                if config.research_backend is not None
                else None
            ),
        )
        if args.command == "ask":
            portfolio = (
                json.loads(args.portfolio.read_text(encoding="utf-8")) if args.portfolio else None
            )
            portfolio_text = (
                args.portfolio_text.read_bytes().decode("utf-8")
                if args.portfolio_text is not None
                else None
            )
            request = TeamRequest(
                question=args.question,
                mode=args.mode,
                portfolio=portfolio,
                portfolio_text=portfolio_text,
                owner_context=args.owner_context.read_bytes().decode("utf-8")
                if args.owner_context is not None
                else None,
                parent_review=str(args.parent_review.expanduser().absolute())
                if args.parent_review is not None
                else None,
                review_kind=args.review_kind,
            )
            result = asyncio.run(run_team(request, config=config, deps=deps))
            sys.stdout.write(result.report_markdown)
            summary = {
                "run_id": result.run_id,
                "completion_reason": result.completion_reason,
                "artifacts": result.artifacts,
            }
        else:
            case = json.loads(args.case.read_text(encoding="utf-8"))
            evaluation = asyncio.run(run_case(case, config, deps, mode=args.mode))
            summary = {
                key: evaluation[key]
                for key in (
                    "run_id",
                    "case_id",
                    "completion_reason",
                    "evaluation_mode",
                    "live_acceptance",
                    "artifacts",
                )
                if key in evaluation
            }
        print(json.dumps(summary), file=sys.stderr if args.command == "ask" else sys.stdout)
        return 0 if summary["completion_reason"] == "complete" else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Input validation can echo private data; stdout never includes raw exception text.
        print(
            json.dumps({"error": "configuration_or_output_error", "category": type(exc).__name__}),
            file=sys.stderr if args.command == "ask" else sys.stdout,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
