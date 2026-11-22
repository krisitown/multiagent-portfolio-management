"""The foreground entry passes user data intact and saves distinct inspectable runs."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.support.thin_fakes import fixed_clock, recording_factory
from portfolio_management.cli import main
from portfolio_management.config import ModelConfig, PromptSet, TeamConfig
from portfolio_management.team import run_team
from portfolio_management.types import TeamDependencies, TeamRequest


def config(output: Path) -> TeamConfig:
    return TeamConfig(
        model=ModelConfig(model_id="offline", provider="scripted"),
        prompts=PromptSet.development(),
        output_dir=output,
        child_output_mode="typed",  # Explicit compatibility fixture; CLI default is direct text.
    )


def snapshot() -> dict:
    return {
        "snapshot_id": "synthetic-snapshot",
        "account_alias": "synthetic-account",
        "source": "host",
        "as_of": "2026-10-30T12:00:00Z",
        "fetched_at": "2026-10-31T12:00:00Z",
        "base_currency": "USD",
        "completeness": "partial",
        "cash": [{"currency": "USD", "availability": "known", "amount": "1200.25"}],
    }


async def test_exact_question_and_snapshot_reach_lead_and_saved_result(tmp_path):
    question = "  Assess this supplied snapshot without inventing marks.  "
    result = await run_team(
        TeamRequest(question=question, mode="portfolio", portfolio=snapshot()),
        config=config(tmp_path),
        deps=TeamDependencies(role_factory=recording_factory, clock=fixed_clock),
    )
    assert result.summary == question
    assert "synthetic-snapshot" in Path(result.artifacts["report"]).read_text()
    saved = json.loads(Path(result.artifacts["request"]).read_text())
    assert saved["portfolio"]["cash"][0]["amount"] == "1200.25"
    assert saved["question"] == question
    assert json.loads(Path(result.artifacts["result"]).read_text())["run_id"] == result.run_id
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert manifest["started_at"] == "2026-10-31T12:00:00+00:00"
    assert manifest["prompt_status"] == "development"


async def test_education_needs_no_portfolio_and_runs_do_not_overwrite(tmp_path):
    request = TeamRequest(question="Explain assignment.", mode="education")
    deps = TeamDependencies(role_factory=recording_factory, clock=fixed_clock)
    first = await run_team(request, config=config(tmp_path), deps=deps)
    second = await run_team(request, config=config(tmp_path), deps=deps)
    assert "no portfolio supplied" in Path(first.artifacts["report"]).read_text()
    assert first.run_id != second.run_id
    assert Path(first.artifacts["report"]).parent != Path(second.artifacts["report"]).parent
    assert Path(first.artifacts["trace"]).is_file()


@pytest.mark.parametrize("change", [{"as_of": "2026-10-30T12:00:00"}, {"cash": "bad"}])
def test_rejects_malformed_snapshot_before_run(change):
    with pytest.raises(ValidationError):
        TeamRequest(question="Assess risk", mode="portfolio", portfolio=snapshot() | change)


def test_cli_ask_writes_narrative_and_separate_json_metadata(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text(config(tmp_path).model_dump_json())
    code = main(
        [
            "ask",
            "--question",
            "Explain assignment.",
            "--mode",
            "education",
            "--config",
            str(path),
            "--output",
            str(tmp_path / "runs"),
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out == "Explain assignment."
    output = json.loads(captured.err)
    assert "report" in output["artifacts"]
    assert Path(output["artifacts"]["report"]).is_file()


def test_cli_rejects_bad_portfolio_without_creating_run(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text(config(tmp_path).model_dump_json())
    portfolio = tmp_path / "bad.json"
    portfolio.write_text('{"snapshot_id":"bad"}')
    out = tmp_path / "runs"
    assert (
        main(
            [
                "ask",
                "--question",
                "Assess risk.",
                "--mode",
                "portfolio",
                "--config",
                str(path),
                "--portfolio",
                str(portfolio),
                "--output",
                str(out),
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"] == "configuration_or_output_error"
    assert not out.exists()
