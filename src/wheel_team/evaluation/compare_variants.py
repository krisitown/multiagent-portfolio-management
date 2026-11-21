"""Compare ordinary saved summaries without importing either competing runtime."""

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any


def content_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def _samples(run: dict[str, Any]) -> dict[tuple[str, str, str, int], dict[str, Any]]:
    values = run.get("results", [run] if "case_id" in run else [])
    if not isinstance(values, list):
        raise ValueError("results must be a list of normalized run summaries")
    output = {}
    for value in values:
        if not isinstance(value, dict) or not value.get("case_id"):
            raise ValueError("Each result needs a case_id")
        repetition = value.get("repetition", 1)
        if not isinstance(repetition, int) or isinstance(repetition, bool) or repetition < 1:
            raise ValueError("repetition must be a positive integer")
        mode = value.get("evaluation_mode")
        key = (
            value["case_id"],
            value.get("role", "team_lead"),
            mode if isinstance(mode, str) else "unknown",
            repetition,
        )
        if key in output:
            raise ValueError("duplicate case/role/mode/repetition identity")
        output[key] = value
    return output


def _number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _totals(values: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "result_count": len(values),
        "failures": sum(
            value.get("completion_reason") != "complete"
            or not str(value.get("final_response") or "").strip()
            or any(f.get("severity") == "error" for f in value.get("findings", []))
            for value in values
        ),
    }
    for field in ("elapsed_seconds", "model_calls", "tool_calls"):
        measurements = [value.get("metrics", {}).get(field) for value in values]
        totals[field] = (
            sum(measurements) if measurements and all(map(_number, measurements)) else None
        )
    usages = [value.get("metrics", {}).get("usage") for value in values]
    fields = ("input_tokens", "output_tokens", "total_tokens")
    totals["usage"] = (
        {field: sum(usage[field] for usage in usages) for field in fields}
        if usages
        and all(
            isinstance(usage, dict) and all(_number(usage.get(field)) for field in fields)
            for usage in usages
        )
        else None
    )
    return totals


def _interval(value: dict[str, Any]) -> tuple[datetime, datetime] | None:
    try:
        start, end = (datetime.fromisoformat(value[key]) for key in ("started_at", "completed_at"))
        if start.utcoffset() is None or end.utcoffset() is None or end < start:
            return None
        return start, end
    except (KeyError, TypeError, ValueError):
        return None


def compare_variants(full: dict[str, Any], thin: dict[str, Any]) -> dict[str, Any]:
    """Use neutral labels; produce evidence/findings, never an automatic quality judgment."""
    before, after = _samples(full), _samples(thin)
    findings: list[dict[str, Any]] = []
    hard: list[dict[str, Any]] = []
    comparable, latency_comparable = True, True

    def note(code: str, reason: str, *, key: Any = None, invalid: bool = True) -> None:
        nonlocal comparable
        findings.append(
            {
                "code": code,
                "severity": "warning",
                "reason": reason,
                "sample": list(key) if key else None,
            }
        )
        if invalid:
            comparable = False

    if not before or not after:
        note("results_missing", "A variant has no saved results")
    if before.keys() != after.keys():
        note("result_coverage_changed", "Unpaired results are retained; no samples were omitted")
    all_keys = before.keys() | after.keys()
    declared_cases = set(full.get("case_ids", [])) | set(thin.get("case_ids", []))
    for label, values in (("A", before), ("B", after)):
        missing = declared_cases - {key[0] for key in values}
        if missing:
            note(
                "declared_cases_missing",
                f"Variant {label}: missing declared cases {sorted(missing)}",
            )
    identities = {key[:3] for key in all_keys}
    for identity in sorted(identities):
        for label, values in (("A", before), ("B", after)):
            repetitions = {key[3] for key in values if key[:3] == identity}
            expected = set(range(1, max(3, max(repetitions, default=0)) + 1))
            if repetitions != expected:
                note(
                    "repetitions_missing",
                    f"Variant {label}: need at least three comparable "
                    f"sequential repetitions; missing {sorted(expected - repetitions)}",
                    key=identity,
                )
    conditions = (
        "case_hash",
        "evidence_hash",
        "evidence_as_of",
        "analysis_as_of",
        "inference_kind",
        "model_id",
        "model_settings",
        "prompt_hashes",
        "knowledge_hashes",
        "limits",
        "tool_specs_hash",
    )
    for label, values in (("A", before), ("B", after)):
        for identity in identities:
            repeated = [sample for key, sample in values.items() if key[:3] == identity]
            for field in conditions:
                if len({content_hash(sample.get(field)) for sample in repeated}) > 1:
                    note(
                        "conditions_drifted",
                        f"Variant {label}: {field} changed between repetitions",
                        key=identity,
                    )
    for key in sorted(before.keys() & after.keys()):
        left, right = before[key], after[key]
        for field in conditions:
            if (
                field not in left
                or left[field] is None
                or field not in right
                or right[field] is None
            ):
                note("provenance_missing", f"Missing comparable {field}", key=key)
            elif left[field] != right[field]:
                note(field + "_changed", f"Effective {field} differs", key=key)
    totals = {}
    intervals = []
    for label, values in (("A", before), ("B", after)):
        totals[label] = _totals(list(values.values()))
        if any(
            totals[label][field] is None
            for field in ("elapsed_seconds", "model_calls", "tool_calls", "usage")
        ):
            note("metrics_missing", f"Variant {label}: unknown metrics remain null")
        for key, value in values.items():
            for finding in value.get("findings", []):
                saved = {**finding, "variant": label, "sample": list(key)}
                findings.append(saved)
                if finding.get("severity") == "error":
                    hard.append(saved)
            interval = _interval(value)
            hardware = value.get("hardware_id")
            if interval is None or not isinstance(hardware, str) or not hardware.strip():
                note(
                    "measurement_context_missing",
                    f"Variant {label}: hardware/time unknown",
                    key=key,
                )
                latency_comparable = False
            else:
                intervals.append((label, key, hardware, interval))
            mode = value.get("evaluation_mode")
            if mode not in ("scripted", "role", "team"):
                note(
                    "evaluation_mode_invalid", "Explicit valid evaluation mode is required", key=key
                )
            if value.get("inference_kind") != "configured_model" or mode not in ("role", "team"):
                note(
                    "non_model_acceptance",
                    "Model comparison requires explicit configured_model inference "
                    "in role/team mode",
                    key=key,
                )
    if len({interval[2] for interval in intervals}) > 1:
        note(
            "hardware_changed", "Controlled latency comparison requires the same measured hardware"
        )
        latency_comparable = False
    for index, interval_left in enumerate(intervals):
        for interval_right in intervals[index + 1 :]:
            if interval_left[2] == interval_right[2] and max(
                interval_left[3][0], interval_right[3][0]
            ) < min(interval_left[3][1], interval_right[3][1]):
                note(
                    "hardware_overlap",
                    "Measurements overlap on the same hardware; rerun sequentially",
                )
                latency_comparable = False
    latency_comparable = latency_comparable and comparable
    left_time, right_time = totals["A"]["elapsed_seconds"], totals["B"]["elapsed_seconds"]
    return {
        "comparison_hash": content_hash([full, thin]),
        "labels": {"A": "full", "B": "thin"},
        "comparable": comparable,
        "latency_comparable": latency_comparable,
        "paired_results": len(before.keys() & after.keys()),
        "case_ids": sorted({key[0] for key in all_keys} | declared_cases),
        "unpaired_results": {
            "A": [list(key) for key in sorted(before.keys() - after.keys())],
            "B": [list(key) for key in sorted(after.keys() - before.keys())],
        },
        "variants": totals,
        "findings": findings,
        "hard_failures": hard,
        "speedup": left_time / right_time
        if latency_comparable
        and right_time
        and not totals["A"]["failures"]
        and not totals["B"]["failures"]
        else None,
        "judgment": None,
        "code_surface": {"A": full.get("code_surface"), "B": thin.get("code_surface")},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("full", type=Path)
    parser.add_argument("thin", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = compare_variants(json.loads(args.full.read_text()), json.loads(args.thin.read_text()))
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
