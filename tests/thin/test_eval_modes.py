"""Offline evidence must never claim live agent acceptance."""

import json
import socket
from pathlib import Path

import pytest

from tests.support.thin_fakes import fixed_clock, recording_factory
from tests.thin.test_entrypoint import config
from wheel_team.cli import main
from wheel_team.evaluation.thin_runner import run_case
from wheel_team.types import TeamDependencies


async def test_scripted_eval_is_offline_and_explicitly_not_live_acceptance(tmp_path, monkeypatch):
    def forbid_network(*args, **kwargs):
        raise AssertionError("network forbidden in scripted evaluation")

    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    case = json.loads(Path("evals/thin/ordinary.json").read_text())
    summary = await run_case(
        case,
        config(tmp_path),
        TeamDependencies(role_factory=recording_factory, clock=fixed_clock),
        mode="scripted",
    )
    manifest = json.loads(Path(summary["artifacts"]["manifest"]).read_text())
    assert summary["case_id"] == "ordinary-education"
    assert manifest["evaluation_mode"] == "scripted"
    assert manifest["live_acceptance"] is False
    assert "Explain assignment" in Path(summary["artifacts"]["report"]).read_text()


@pytest.mark.parametrize("mode", ["role", "team", "invalid"])
async def test_scripted_provider_cannot_be_mislabelled_as_live(tmp_path, mode):
    with pytest.raises(ValueError):
        await run_case(
            {"case_id": "ordinary", "question": "Explain assignment", "mode": "education"},
            config(tmp_path),
            TeamDependencies(role_factory=recording_factory),
            mode=mode,
        )


def test_cli_eval_accepts_single_case_and_explicit_mode(tmp_path, capsys):
    path = tmp_path / "config.json"
    path.write_text(config(tmp_path).model_dump_json())
    assert (
        main(
            [
                "eval",
                "--case",
                "evals/thin/ordinary.json",
                "--mode",
                "scripted",
                "--config",
                str(path),
                "--output",
                str(tmp_path / "runs"),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["evaluation_mode"] == "scripted"


@pytest.mark.parametrize("case_id", [None, "", "../outside", "case/escape", "  "])
async def test_invalid_case_identity_is_rejected_before_creating_artifacts(tmp_path, case_id):
    case = {"question": "Explain assignment", "mode": "education"}
    if case_id is not None:
        case["case_id"] = case_id
    with pytest.raises(ValueError, match="case_id"):
        await run_case(case, config(tmp_path), TeamDependencies(), mode="scripted")
    assert not list(tmp_path.iterdir())


async def test_bad_output_and_redundant_research_are_findings_without_golden_trajectory(tmp_path):
    from wheel_team.evaluation import thin_runner

    assert hasattr(thin_runner, "evaluate_findings"), "missing outcome and trace findings"
    findings = thin_runner.evaluate_findings(
        {"mode": "discovery"},
        {"completion_reason": "complete", "summary": "", "candidate_slots": []},
        [
            {
                "event": "tool_call_started",
                "name": "market_query",
                "inputs": {"query": "same"},
                "invocation_id": "analyst-1",
            }
        ]
        * 4,
    )
    found = {finding["code"] for finding in findings}
    assert {
        "final_output_missing",
        "candidate_slots_missing",
        "redundant_research",
        "looping",
    } <= found
    assert any(finding["severity"] == "error" for finding in findings)


async def test_team_mode_uses_actual_finance_and_persisted_independent_oracle(tmp_path):
    from tests.support.thin_sdk import ScriptModel, output
    from tests.thin.test_role_isolation import model_config

    oracle = json.loads(Path("tests/fixtures/finance/option_expected.json").read_text())[
        "roll_cases"
    ][0]
    inputs = oracle["inputs"]
    models = iter(
        [
            ScriptModel(
                output("research_batch", jobs=[{"job_id": "roll", "question": "Calculate roll"}]),
                output("TeamResult", summary="Roll realizes the old loss; cash is separate."),
            ),
            ScriptModel(
                output("finance_calculate", formula="roll_economics", inputs=inputs),
                output("ResearchResult", job_id="roll", outcome="success", summary="Calculated"),
            ),
        ]
    )
    case = {
        "case_id": "real-roll",
        "question": "Assess roll",
        "mode": "education",
        "financial_oracles": [
            {
                "id": oracle["id"],
                "formula": "roll_economics",
                "match_inputs": {"quantity": "2"},
                "expected": oracle["expected"],
            }
        ],
    }
    summary = await run_case(
        case,
        model_config(tmp_path),
        TeamDependencies(model_factory=lambda *a: next(models), clock=fixed_clock),
        mode="team",
    )
    assert summary["completion_reason"] == "complete"
    assert summary["metrics"]["tool_calls"] == 2
    assert "calculation_evidence_missing" not in {f["code"] for f in summary["findings"]}
    assert "financial_oracle_mismatch" not in {f["code"] for f in summary["findings"]}
    assert any(
        row["record"]["outputs"]["old_leg_realized_total"] == "-600"
        for row in summary["evidence"]
        if row["kind"] == "calculation"
    )


def test_bad_arithmetic_is_detected_using_literal_oracle():
    from wheel_team.evaluation.thin_runner import evaluate_findings

    findings = evaluate_findings(
        {
            "financial_oracles": [
                {
                    "id": "roll",
                    "formula": "roll_economics",
                    "expected": {"old_leg_realized_total": "-600"},
                }
            ]
        },
        {"summary": "Looks profitable"},
        [],
        [
            {
                "kind": "calculation",
                "record": {
                    "formula": "roll_economics",
                    "outputs": {"old_leg_realized_total": "600"},
                },
            }
        ],
    )
    assert "financial_oracle_mismatch" in {f["code"] for f in findings}


async def test_team_mode_rejects_live_provider_or_tool_mocks_before_run(tmp_path):
    from tests.thin.test_role_isolation import model_config

    case = {"case_id": "isolation", "question": "Research"}
    with pytest.raises(ValueError, match="controlled fixture"):
        await run_case(
            case, model_config(tmp_path), TeamDependencies(market_provider=object()), mode="team"
        )
    with pytest.raises(ValueError, match="actual tools"):
        await run_case(
            case | {"mocked_tools": {"market_query": []}},
            model_config(tmp_path),
            TeamDependencies(),
            mode="team",
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "name", ["conditional", "stale_capacity", "outage", "analyst_failure", "source_attack"]
)
def test_thin_scenarios_preserve_canonical_world_and_source_provenance(name):
    import hashlib

    adapted = json.loads(Path(f"evals/thin/{name}.json").read_text())
    source_path = Path(adapted["provenance"]["source_path"])
    source = json.loads(source_path.read_text())
    assert (
        hashlib.sha256(source_path.read_bytes()).hexdigest()
        == adapted["provenance"]["source_sha256"]
    )
    assert adapted["portfolio"] == source["request"].get("snapshot")
    assert adapted["fixtures"]["records"] == source["fixtures"]["records"]
    assert adapted["fixtures"]["clock"] == source["fixtures"]["clock"]


async def test_controlled_world_never_falls_back_to_live_sources():
    from wheel_team.contracts import MarketQuery
    from wheel_team.evaluation.thin_runner import CaseMarketProvider

    case = json.loads(Path("evals/thin/outage.json").read_text())
    provider = CaseMarketProvider(case["fixtures"])
    result = await provider.query(MarketQuery(query_id="unavailable", capability="quotes"))
    assert result.status == "unavailable"
    assert result.evidence == ()


async def test_team_cannot_be_mislabelled_as_a_single_role_result(tmp_path):
    from tests.thin.test_role_isolation import model_config

    with pytest.raises(ValueError, match="team_lead"):
        await run_case(
            {"case_id": "wrong-scope", "role": "analyst", "question": "Research"},
            model_config(tmp_path),
            TeamDependencies(),
            mode="team",
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("variant", ["empty", "different", "identical"])
async def test_team_rejects_provider_overrides_independent_of_declared_case(tmp_path, variant):
    import copy

    from tests.support.thin_sdk import ScriptModel, output
    from tests.thin.test_role_isolation import model_config
    from wheel_team.evaluation.thin_runner import CaseMarketProvider

    case = json.loads(Path("evals/thin/conditional.json").read_text())
    supplied = copy.deepcopy(case["fixtures"])
    if variant == "empty":
        supplied = {}
    elif variant == "different":
        supplied["market_pages"]["fundamentals"]["evidence"][0]["content"] = {"altered": True}
    model = ScriptModel(output("TeamResult", summary="Controlled answer"))
    with pytest.raises(ValueError, match="case-bound"):
        await run_case(
            case,
            model_config(tmp_path),
            TeamDependencies(
                model_factory=lambda *a: model,
                market_provider=CaseMarketProvider(supplied),
                clock=fixed_clock,
            ),
            mode="team",
        )
    assert not list(tmp_path.iterdir())


async def test_fixture_analysis_clock_is_separate_from_sequential_wall_measurements(tmp_path):
    from datetime import datetime

    from wheel_team.evaluation.compare_variants import compare_variants

    case = {
        "case_id": "frozen-asof",
        "question": "Explain assignment",
        "mode": "education",
        "fixtures": {"clock": "2026-10-30T14:00:00+00:00"},
        "hardware_id": "same-host",
    }
    first = await run_case(
        case, config(tmp_path), TeamDependencies(clock=fixed_clock), mode="scripted"
    )
    second = await run_case(
        case | {"repetition": 2},
        config(tmp_path),
        TeamDependencies(clock=fixed_clock),
        mode="scripted",
    )
    manifest = json.loads(Path(first["artifacts"]["manifest"]).read_text())
    assert manifest["started_at"] == "2026-10-30T14:00:00+00:00"
    assert first["analysis_as_of"] == second["analysis_as_of"] == case["fixtures"]["clock"]
    assert datetime.fromisoformat(first["started_at"]) < datetime.fromisoformat(
        first["completed_at"]
    )
    assert datetime.fromisoformat(first["completed_at"]) <= datetime.fromisoformat(
        second["started_at"]
    )
    assert datetime.fromisoformat(second["started_at"]) < datetime.fromisoformat(
        second["completed_at"]
    )
    comparison = compare_variants({"results": [first]}, {"results": [second]})
    assert "hardware_overlap" not in {f["code"] for f in comparison["findings"]}
