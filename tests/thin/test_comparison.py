"""Saved comparisons must preserve failures and reject unequal measurements."""

import copy
import importlib
import json
from datetime import UTC, datetime, timedelta

import pytest


def compare(left, right):
    try:
        module = importlib.import_module("wheel_team.evaluation.compare_variants")
    except ModuleNotFoundError:
        pytest.fail("missing thin saved-variant comparison")
    return module.compare_variants(left, right)


def sample(repetition=1, **changes):
    value = {
        "case_id": "synthetic",
        "repetition": repetition,
        "evaluation_mode": "team",
        "inference_kind": "configured_model",
        "role": "team_lead",
        "case_hash": "case",
        "evidence_hash": "data",
        "evidence_as_of": "2026-10-30T14:00:00+00:00",
        "analysis_as_of": "2026-10-30T14:00:00+00:00",
        "model_id": "fixed-model",
        "model_settings": {"max_output_tokens": 4096},
        "prompt_hashes": {"team_lead": "p"},
        "knowledge_hashes": {"core": "k"},
        "tool_specs_hash": "tools",
        "limits": {"calls": 100},
        "hardware_id": "independent-left",
        "started_at": "2026-10-31T12:00:00+00:00",
        "completed_at": "2026-10-31T12:01:00+00:00",
        "completion_reason": "complete",
        "metrics": {
            "elapsed_seconds": 60,
            "model_calls": 2,
            "tool_calls": 1,
            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        },
        "final_response": "Supported synthetic response",
        "findings": [],
    }
    return value | changes


def trial(hardware="independent-left", offset=0):
    return {
        "results": [
            sample(
                i,
                hardware_id=hardware,
                started_at=(
                    datetime(2026, 10, 31, 12, tzinfo=UTC) + timedelta(minutes=offset + 2 * i)
                ).isoformat(),
                completed_at=(
                    datetime(2026, 10, 31, 12, tzinfo=UTC) + timedelta(minutes=offset + 2 * i + 1)
                ).isoformat(),
            )
            for i in range(1, 4)
        ]
    }


def codes(report):
    return {finding["code"] for finding in report["findings"]}


def test_equal_trials_are_comparable_without_automatic_quality_verdict():
    report = compare(trial(), trial(offset=10))
    assert report["comparable"] is True
    assert report["paired_results"] == 3
    assert report["latency_comparable"] is True
    assert report["speedup"] == 1
    assert report["judgment"] is None
    assert report["variants"]["A"]["failures"] == 0


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("case_hash", "different", "case_hash_changed"),
        ("evidence_hash", "different", "evidence_hash_changed"),
        ("evidence_as_of", "different", "evidence_as_of_changed"),
        ("model_id", "other", "model_id_changed"),
        ("model_settings", {}, "model_settings_changed"),
        ("prompt_hashes", {"team_lead": "other"}, "prompt_hashes_changed"),
        ("knowledge_hashes", {}, "knowledge_hashes_changed"),
        ("limits", {"calls": 99}, "limits_changed"),
        ("tool_specs_hash", "other", "tool_specs_hash_changed"),
    ],
)
def test_equal_case_id_cannot_hide_non_equivalent_conditions(field, value, code):
    left, right = trial(), trial("independent-right")
    right["results"][0][field] = value
    report = compare(left, right)
    assert code in codes(report)
    assert report["comparable"] is False
    assert report["speedup"] is None
    assert report["judgment"] is None


def test_missing_results_and_repetitions_remain_visible():
    left, right = trial(), trial("independent-right")
    right["results"] = right["results"][:1]
    report = compare(left, right)
    assert {"result_coverage_changed", "repetitions_missing"} <= codes(report)
    assert report["unpaired_results"]["A"] == [
        ["synthetic", "team_lead", "team", 2],
        ["synthetic", "team_lead", "team", 3],
    ]
    assert report["variants"]["B"]["result_count"] == 1


def test_one_missing_metric_cannot_be_summed_as_zero_or_excluded():
    left, right = trial(), trial("independent-right")
    del right["results"][1]["metrics"]["model_calls"]
    right["results"][0]["metrics"]["usage"] = None
    report = compare(left, right)
    assert "metrics_missing" in codes(report)
    assert report["variants"]["B"]["model_calls"] is None
    assert report["variants"]["B"]["usage"] is None
    assert report["comparable"] is False


def test_overlapping_shared_hardware_invalidates_latency_even_with_equal_data():
    report = compare(trial(), trial())
    assert "hardware_overlap" in codes(report)
    assert report["latency_comparable"] is False
    assert report["speedup"] is None


def test_failed_or_empty_outputs_and_hard_findings_cannot_disappear():
    left, right = trial(), trial("independent-right")
    right["results"][0].update(
        completion_reason="failed",
        final_response="",
        findings=[{"code": "unsupported_claim", "severity": "error", "reason": "No evidence"}],
    )
    report = compare(left, right)
    assert report["variants"]["B"]["failures"] == 1
    assert "unsupported_claim" in codes(report)
    assert report["hard_failures"][0]["code"] == "unsupported_claim"
    assert report["judgment"] is None


def test_duplicate_sample_identity_is_rejected_and_inputs_are_not_mutated():
    left, right = trial(), trial("independent-right")
    original = copy.deepcopy((left, right))
    compare(left, right)
    assert (left, right) == original
    right["results"].append(copy.deepcopy(right["results"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        compare(left, right)


def test_packet_contains_output_evidence_trace_and_hash_bound_review_placeholder(tmp_path):
    module = importlib.import_module("wheel_team.evaluation.thin_runner")
    run = sample(
        final_response="A conditional synthetic answer",
        evidence=[{"evidence_id": "e1"}],
        trace=[{"event": "tool_call_started", "name": "market_query"}],
    )
    assert hasattr(module, "write_review_packet"), "missing ordinary review packet export"
    packet_dir = module.write_review_packet(run, tmp_path / "review")
    packet = json.loads((packet_dir / "review-packet.json").read_text())
    assert packet["run"]["final_response"] == "A conditional synthetic answer"
    assert packet["run"]["evidence"] == [{"evidence_id": "e1"}]
    assert packet["run"]["trace"][0]["name"] == "market_query"
    assert len(packet["packet_hash"]) == 64
    assessment = json.loads((packet_dir / "assessment.json").read_text())
    assert assessment["packet_hash"] == packet["packet_hash"]
    assert assessment["verdict"] is None
    assert "conditional synthetic answer" in (packet_dir / "review.md").read_text()


def test_matched_settings_drift_across_repetitions_is_not_a_controlled_trial():
    left, right = trial(), trial("independent-right")
    left["results"][1]["model_settings"] = right["results"][1]["model_settings"] = {"changed": True}
    report = compare(left, right)
    assert "conditions_drifted" in codes(report)
    assert report["comparable"] is False


def test_missing_provenance_and_times_cannot_be_treated_as_equal_unknowns():
    left, right = trial(), trial("independent-right")
    for value in left["results"] + right["results"]:
        del value["evidence_hash"]
        value["started_at"] = None
    report = compare(left, right)
    assert {"provenance_missing", "measurement_context_missing"} <= codes(report)
    assert report["comparable"] is False


def test_scripted_results_are_engineering_evidence_even_when_three_trials_match():
    left, right = trial(), trial("independent-right")
    for value in left["results"] + right["results"]:
        value["inference_kind"] = "injected_sdk_transport"
    report = compare(left, right)
    assert "non_model_acceptance" in codes(report)
    assert report["comparable"] is False
    assert report["speedup"] is None


def test_declared_case_missing_from_both_variants_is_not_silently_omitted():
    left, right = trial(), trial("independent-right")
    left["case_ids"] = right["case_ids"] = ["synthetic", "absent"]
    report = compare(left, right)
    assert "declared_cases_missing" in codes(report)
    assert report["case_ids"] == ["absent", "synthetic"]
    assert report["comparable"] is False


def test_packet_reexport_preserves_human_assessment_and_rejects_different_packet(tmp_path):
    from wheel_team.evaluation.thin_runner import write_review_packet

    target = write_review_packet(sample(), tmp_path / "review")
    assessment = json.loads((target / "assessment.json").read_text())
    assessment.update(verdict="needs_revision", rationale="Inspect references")
    (target / "assessment.json").write_text(json.dumps(assessment))
    write_review_packet(sample(), target)
    assert json.loads((target / "assessment.json").read_text())["verdict"] == "needs_revision"
    with pytest.raises(ValueError, match="different packet"):
        write_review_packet(sample(final_response="Changed"), target)


@pytest.mark.parametrize(
    "kind", ["missing", None, "", "unknown", "scripted", "injected_sdk_transport"]
)
def test_model_comparison_requires_explicit_configured_inference_for_every_sample(kind):
    left, right = trial(), trial(offset=10)
    for value in right["results"]:
        if kind == "missing":
            value.pop("inference_kind")
        else:
            value["inference_kind"] = kind
    report = compare(left, right)
    assert "non_model_acceptance" in codes(report)
    assert report["comparable"] is False
    assert report["latency_comparable"] is False
    assert report["speedup"] is None
    assert report["variants"]["B"]["result_count"] == 3


@pytest.mark.parametrize(
    "mode", ["missing", None, "", "unknown", "scripted", ["team"], {"mode": "team"}]
)
def test_invalid_or_offline_modes_cannot_pair_as_a_model_comparison(mode):
    left, right = trial(), trial(offset=10)
    for value in left["results"] + right["results"]:
        if mode == "missing":
            value.pop("evaluation_mode")
        else:
            value["evaluation_mode"] = mode
    report = compare(left, right)
    assert report["comparable"] is False
    assert report["latency_comparable"] is False
    assert report["speedup"] is None
    assert {"evaluation_mode_invalid", "non_model_acceptance"} & codes(report)
    assert report["paired_results"] == 3


def test_configured_role_and_team_modes_must_match_for_speed_comparison():
    left, right = trial(), trial(offset=10)
    for value in right["results"]:
        value["evaluation_mode"] = "role"
    report = compare(left, right)
    assert "result_coverage_changed" in codes(report)
    assert report["comparable"] is False
    assert report["speedup"] is None


@pytest.mark.parametrize("change_one_sample", [False, True])
def test_different_hardware_cannot_support_an_architecture_speed_ratio(change_one_sample):
    left, right = trial(), trial(offset=10)
    selected = right["results"][:1] if change_one_sample else right["results"]
    for value in selected:
        value["hardware_id"] = "unequal-hardware"
    report = compare(left, right)
    assert "hardware_changed" in codes(report)
    assert report["latency_comparable"] is False
    assert report["speedup"] is None


def test_unequal_analysis_clock_is_non_equivalent_even_with_equal_evidence_time():
    left, right = trial(), trial(offset=10)
    right["results"][0]["analysis_as_of"] = "2026-10-31T14:00:00+00:00"
    report = compare(left, right)
    assert "analysis_as_of_changed" in codes(report)
    assert report["comparable"] is False
    assert report["speedup"] is None
