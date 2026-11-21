"""Consume plain-vanilla protection once without implying broker capacity."""

import hashlib
import json
from decimal import Decimal, DecimalException, localcontext

from wheel_team.contracts import (
    Availability,
    CalculationRecord,
    Finding,
    JsonValue,
    PortfolioSnapshot,
    Position,
)
from wheel_team.contracts.evidence import ResultState
from wheel_team.domain.money import arithmetic_context, calculation_record

ZERO = Decimal("0")


def _record(
    formula: str,
    parameters: dict[str, JsonValue],
    *,
    outputs: dict[str, Decimal],
    units: dict[str, str],
    currencies: dict[str, str] | None = None,
    availability: dict[str, Availability] | None = None,
    inputs: dict[str, Decimal] | None = None,
    refs: tuple[str, ...] = (),
    findings: tuple[Finding, ...] = (),
    status: ResultState = "success",
    reason: str | None = None,
) -> CalculationRecord:
    """Enrich the reviewed record builder with independently denominated fields.

    The final hash includes availability and currencies, so adding an unknown
    or changing a denomination cannot leave the calculation identity unchanged.
    """
    base = calculation_record(
        formula,
        inputs or {},
        parameters,
        outputs=outputs,
        units=units,
        input_refs=refs,
        findings=findings,
        status=status,
        reason=reason,
    )
    record = CalculationRecord.model_validate(
        base.model_dump()
        | {
            "currencies": currencies or {},
            "availability": base.availability | (availability or {}),
        }
    )
    encoded = json.dumps(
        record.model_dump(mode="json", exclude={"calculation_id", "computed_at"}),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return record.model_copy(
        update={"calculation_id": "calculation:" + hashlib.sha256(encoded).hexdigest()}
    )


def _unsupported(position: Position) -> bool:
    contract = position.contract
    return contract.instrument_type not in {"stock", "option"} or bool(
        contract.deliverable or contract.adjustment
    )


def _family(position: Position) -> tuple[object, ...]:
    contract = position.contract
    # Strike remains in each allocation's input identity and residual gap.
    return (
        contract.underlying_id,
        contract.expiry,
        contract.option_right,
        contract.currency,
        contract.multiplier,
        contract.deliverable,
        contract.adjustment,
    )


def match_coverage(snapshot: PortfolioSnapshot) -> CalculationRecord:
    """Match signed integral options and explicitly unencumbered eligible shares.

    This is contractual expiry protection. Strike-gap exposure excludes premium,
    fees and early assignment funding; it never establishes broker margin.
    """
    rows = snapshot.positions
    refs = (snapshot.snapshot_id,) + tuple(row.position_id for row in rows or ())
    parameters: dict[str, JsonValue] = {
        "snapshot": snapshot.model_dump(mode="json"),
        "coverage_allocations": [],
        "method": "one_use_exact_family_expiry_protection",
        "funding_or_margin_verified": False,
        "strike_gap_excludes": ["premiums", "fees", "early_assignment_funding"],
    }
    unknown = {"broker_margin": Availability.UNKNOWN}
    if rows is None:
        reason = "Position collection is absent; coverage is unknown"
        return _record(
            "match_coverage",
            parameters,
            outputs={},
            units={},
            refs=refs,
            availability=unknown | {"unmatched_short_contracts": Availability.UNKNOWN},
            status="unavailable",
            reason=reason,
            findings=(
                Finding(
                    code="positions_unknown", severity="warning", affected_ids=refs, reason=reason
                ),
            ),
        )
    invalid = len({row.position_id for row in rows}) != len(rows)
    for row in rows:
        q = row.quantity
        if row.contract.instrument_type == "option" and (q == 0 or q != q.to_integral_value()):
            invalid = True
        if row.encumbered_quantity is not None and (
            row.encumbered_quantity < 0 or row.encumbered_quantity > max(ZERO, row.quantity)
        ):
            invalid = True
    if invalid:
        reason = "Duplicate IDs, invalid encumbrance, or nonintegral/zero option count"
        return _record(
            "match_coverage",
            parameters,
            outputs={},
            units={},
            refs=refs,
            status="invalid_data",
            reason=reason,
            findings=(
                Finding(
                    code="invalid_coverage_input",
                    severity="error",
                    affected_ids=refs,
                    reason=reason,
                ),
            ),
        )
    findings = [
        Finding(
            code="unsupported_coverage",
            severity="warning",
            affected_ids=(row.position_id,),
            reason="Instrument or explicit/adjusted deliverable is not modeled",
        )
        for row in rows
        if _unsupported(row)
    ]
    options = [row for row in rows if row.contract.instrument_type == "option"]
    shorts = sorted((row for row in options if row.quantity < 0), key=lambda row: row.position_id)
    longs = sorted(
        (row for row in options if row.quantity > 0 and not _unsupported(row)),
        key=lambda row: row.position_id,
    )
    stocks = sorted(
        (
            row
            for row in rows
            if row.contract.instrument_type == "stock"
            and row.quantity > 0
            and not _unsupported(row)
        ),
        key=lambda row: row.position_id,
    )
    allocations: list[JsonValue] = []
    try:
        with localcontext(arithmetic_context()):
            long_left = {row.position_id: row.quantity for row in longs}
            share_left = {
                row.position_id: row.quantity - row.encumbered_quantity
                for row in stocks
                if row.encumbered_quantity is not None
            }
            matched = ZERO
            shares_matched = ZERO
            unmatched = ZERO
            gaps: dict[str, Decimal] = {}
            for short in shorts:
                remaining = -short.quantity
                contract = short.contract
                if _unsupported(short):
                    unmatched += remaining
                    continue
                assert contract.strike is not None and contract.multiplier is not None
                for long in longs:
                    if remaining == 0 or _family(short) != _family(long):
                        continue
                    allocated = min(remaining, long_left[long.position_id])
                    if allocated == 0:
                        continue
                    assert long.contract.strike is not None
                    difference = (
                        contract.strike - long.contract.strike
                        if contract.option_right == "put"
                        else long.contract.strike - contract.strike
                    )
                    gaps[contract.currency] = (
                        gaps.get(contract.currency, ZERO)
                        + max(ZERO, difference) * contract.multiplier * allocated
                    )
                    long_left[long.position_id] -= allocated
                    remaining -= allocated
                    matched += allocated
                    allocations.append(
                        {
                            "short_position_id": short.position_id,
                            "protection_position_id": long.position_id,
                            "contracts": str(allocated),
                        }
                    )
                if contract.option_right == "call" and remaining > 0:
                    for stock in stocks:
                        if (
                            stock.contract.instrument_id != contract.underlying_id
                            or stock.currency != contract.currency
                        ):
                            continue
                        if stock.position_id not in share_left:
                            findings.append(
                                Finding(
                                    code="share_encumbrance_unknown",
                                    severity="warning",
                                    affected_ids=(stock.position_id,),
                                    reason="Free share capacity needs known encumbrance",
                                )
                            )
                            continue
                        allocated = min(
                            remaining, share_left[stock.position_id] // contract.multiplier
                        )
                        if allocated <= 0:
                            continue
                        share_left[stock.position_id] -= allocated * contract.multiplier
                        remaining -= allocated
                        shares_matched += allocated
                        allocations.append(
                            {
                                "short_position_id": short.position_id,
                                "protection_position_id": stock.position_id,
                                "contracts": str(allocated),
                            }
                        )
                unmatched += remaining
    except DecimalException:
        reason = "Coverage quantities exceed supported Decimal arithmetic"
        return _record(
            "match_coverage",
            parameters,
            outputs={},
            units={},
            refs=refs,
            status="invalid_data",
            reason=reason,
            findings=(
                Finding(code="invalid_coverage_arithmetic", severity="error", reason=reason),
            ),
        )
    parameters["coverage_allocations"] = allocations
    parameters["matched_strike_gap_by_currency"] = {
        key: str(value) for key, value in sorted(gaps.items())
    }
    outputs = {
        "matched_option_contracts": matched,
        "share_covered_call_contracts": shares_matched,
        "unmatched_short_contracts": unmatched,
    }
    units = {key: "contracts" for key in outputs}
    currencies: dict[str, str] = {}
    gap_currencies = set(gaps) or {row.currency for row in rows}
    if len(gap_currencies) == 1:
        currency = next(iter(gap_currencies))
        outputs["matched_strike_gap_exposure"] = gaps.get(currency, ZERO)
        units["matched_strike_gap_exposure"] = "currency"
        currencies["matched_strike_gap_exposure"] = currency
    elif len(gap_currencies) > 1:
        unknown["matched_strike_gap_exposure"] = Availability.UNKNOWN
        if len(gaps) > 1:
            findings.append(
                Finding(
                    code="coverage_fx_missing",
                    severity="warning",
                    reason="Cross-currency strike gaps need explicit FX before aggregation",
                )
            )
    return _record(
        "match_coverage",
        parameters,
        outputs=outputs,
        units=units,
        currencies=currencies,
        availability=unknown,
        refs=refs,
        findings=tuple(findings),
        status="unavailable" if findings else "success",
        reason="Some coverage cannot be established; see findings" if findings else None,
    )
