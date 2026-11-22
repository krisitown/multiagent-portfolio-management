"""Execution and fixture problems must not be reported as investment judgment failures."""

import json
from pathlib import Path

import pytest

from tests.support.thin_fakes import fixed_clock
from tests.thin.test_entrypoint import config
from portfolio_management.evaluation import thin_runner
from portfolio_management.types import TeamDependencies


def categorize(result, findings=(), trace=(), evidence=()):
    assert hasattr(thin_runner, "categorize_outcomes"), "missing four outcome categories"
    return thin_runner.categorize_outcomes(result, list(findings), list(trace), list(evidence))


@pytest.mark.parametrize("error", ["TimeoutError", "ValidationError", "ToolProtocolError"])
def test_protocol_failure_leaves_investment_judgment_unknown(error):
    categories = categorize(
        {"completion_reason": "failed", "summary": "", "error_category": error},
        [{"code": "final_output_missing", "severity": "error"}],
        [{"event": "role_failed", "category": error}],
    )
    assert categories["execution_protocol"]["status"] == "observed"
    assert categories["facts_investment_reasoning"]["status"] == "unknown"
    assert categories["facts_investment_reasoning"]["judgment"] is None


def test_mock_and_grader_incompatibility_do_not_become_bad_model_reasoning():
    categories = categorize(
        {"completion_reason": "complete", "summary": "A readable answer"},
        [{"code": "calculation_evidence_missing", "severity": "error"}],
        [{"event": "mock_tool_incompatible", "category": "arguments_mismatch"}],
    )
    assert categories["mock_grader_compatibility"]["status"] == "observed"
    assert categories["facts_investment_reasoning"]["status"] == "unknown"
    assert categories["facts_investment_reasoning"]["judgment"] is None


def test_actual_unsupported_fact_is_separate_from_protocol_and_needs_human_judgment():
    categories = categorize(
        {"completion_reason": "complete", "summary": "An unsupported claim"},
        [{"code": "financial_oracle_mismatch", "severity": "error"}],
    )
    assert categories["execution_protocol"]["status"] == "not_observed"
    assert categories["facts_investment_reasoning"]["status"] == "observed"
    assert categories["facts_investment_reasoning"]["judgment"] is None


def test_unavailable_or_stale_evidence_is_separate_from_investment_judgment():
    categories = categorize(
        {"completion_reason": "partial", "summary": "Current quote unavailable"},
        [{"code": "stale_entry_quote", "severity": "warning"}],
        [
            {
                "event": "mock_tool_result",
                "name": "market_query",
                "result": {"status": "unavailable", "reason": "Source outage"},
            }
        ],
    )
    assert categories["evidence_availability"]["status"] == "observed"
    assert categories["facts_investment_reasoning"]["status"] == "pending_review"
    assert categories["facts_investment_reasoning"]["judgment"] is None


async def test_categories_are_saved_in_summary_and_readable_review_packet(tmp_path):
    summary = await thin_runner.run_case(
        {
            "case_id": "arithmetic-only",
            "question": "Explain 2 + 3 using labelled example numbers.",
            "mode": "education",
        },
        config(tmp_path / "runs"),
        TeamDependencies(clock=fixed_clock),
        mode="scripted",
    )
    assert set(summary["outcome_categories"]) == {
        "execution_protocol",
        "mock_grader_compatibility",
        "facts_investment_reasoning",
        "evidence_availability",
    }
    saved = json.loads(
        (Path(summary["artifacts"]["manifest"]).parent / "evaluation-summary.json").read_text()
    )
    assert saved["outcome_categories"] == summary["outcome_categories"]
    packet = thin_runner.write_review_packet(summary, tmp_path / "review")
    assert "Outcome categories" in (packet / "review.md").read_text()
    assert (
        json.loads((packet / "review-packet.json").read_text())["run"]["outcome_categories"]
        == summary["outcome_categories"]
    )


@pytest.mark.parametrize("mode", ["role", "team"])
async def test_fictional_issuer_cases_cannot_run_as_actual_model_acceptance(tmp_path, mode):
    from tests.thin.test_role_isolation import model_config

    case = json.loads(Path("evals/thin/conditional.json").read_text())
    with pytest.raises(ValueError, match="retired"):
        await thin_runner.run_case(case, model_config(tmp_path), TeamDependencies(), mode=mode)
    assert not tmp_path.exists() or not list(tmp_path.iterdir())


async def test_connected_acceptance_rejects_replay_or_absent_backends_before_artifacts(tmp_path):
    from tests.thin.test_role_isolation import model_config

    case = {
        "case_id": "public-companies",
        "purpose": "connected_acceptance",
        "mode": "focused",
        "question": "Research KO, MSFT and JNJ using current public sources.",
    }
    with pytest.raises(ValueError, match="connected"):
        await thin_runner.run_case(case, model_config(tmp_path), TeamDependencies(), mode="team")
    assert not tmp_path.exists() or not list(tmp_path.iterdir())


async def test_replay_without_finalized_selected_capture_fails_before_inference(tmp_path):
    case = {
        "case_id": "recorded-companies",
        "purpose": "captured_replay",
        "mode": "focused",
        "question": "Explain the recorded KO observation.",
        "capture": {"capture_id": "unselected"},
    }
    with pytest.raises(ValueError, match="finalized"):
        await thin_runner.run_case(case, config(tmp_path), TeamDependencies(), mode="scripted")
    assert not tmp_path.exists() or not list(tmp_path.iterdir())


async def test_missing_role_mock_is_recorded_as_fixture_incompatibility(tmp_path):
    from tests.support.thin_sdk import ScriptModel, output
    from tests.thin.test_role_isolation import model_config

    model = ScriptModel(
        output("public_search", query="KO", limit=5),
        output(
            "ResearchResult",
            job_id="coverage",
            outcome="success",
            summary="Cannot research with unconfigured controlled data",
        ),
    )
    summary = await thin_runner.run_case(
        {"case_id": "mock-coverage", "role": "analyst", "question": "Research KO"},
        model_config(tmp_path),
        TeamDependencies(model_factory=lambda *a: model, clock=fixed_clock),
        mode="role",
    )
    assert summary["outcome_categories"]["mock_grader_compatibility"]["status"] == "observed"
    assert summary["outcome_categories"]["facts_investment_reasoning"]["status"] == "unknown"


async def test_connected_acceptance_cannot_be_satisfied_by_replay_descriptions(tmp_path):
    from tests.thin.test_role_isolation import model_config

    class Replay:
        def describe(self):
            return {"mode": "captured_replay"}

    class Web:
        def describe(self):
            return {"mode": "connected"}

    with pytest.raises(ValueError, match="connected"):
        await thin_runner.run_case(
            {
                "case_id": "connected-required",
                "purpose": "connected_acceptance",
                "question": "Research actual public companies.",
            },
            model_config(tmp_path),
            TeamDependencies(market_provider=Replay(), research_backend=Web()),
            mode="team",
        )
    assert not list(tmp_path.iterdir())


async def test_recorded_public_source_mocks_preserve_exact_metadata_without_live_calls(tmp_path):
    from tests.support.thin_sdk import ScriptModel, output
    from tests.thin.test_role_isolation import model_config

    case = json.loads(Path("evals/thin/role/recorded_msft_public_sources.json").read_text())
    search_args = case["mocked_tools"]["public_search"]["by_arguments"][0]["arguments"]
    url = "https://www.microsoft.com/en-us/investor/earnings/fy-2026-q3/balance-sheets"
    model = ScriptModel(
        output("public_search", **search_args),
        output("fetch_sources", urls=[url]),
        output(
            "ResearchResult",
            job_id="recorded",
            outcome="success",
            summary="Recorded page; source publication time remains unknown.",
        ),
    )
    summary = await thin_runner.run_case(
        case,
        model_config(tmp_path),
        TeamDependencies(model_factory=lambda *a: model, clock=fixed_clock),
        mode="role",
    )
    assert summary["metrics"]["tool_calls"] == 2
    assert summary["outcome_categories"]["mock_grader_compatibility"]["status"] == "not_observed"
    fetched = next(
        row["result"]
        for row in summary["trace"]
        if row["event"] == "mock_tool_result" and row["name"] == "fetch_sources"
    )
    assert fetched == case["mocked_tools"]["fetch_sources"]["by_arguments"][0]["response"]
    # This path no longer reads full metadata through an exposed evidence reader.
    # The unchanged fetched payload still states publication-time uncertainty.
    assert fetched["data"]["source_time_note"] == (
        "Fetch time is not observation or publication time"
    )
    assert summary["outcome_categories"]["evidence_availability"]["status"] == "not_observed"
    assert summary["live_acceptance"] is False
