"""Decimal validation and reproducible calculation-record construction."""

import hashlib
import json
import re
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    Underflow,
)

from portfolio_management.contracts import Availability, CalculationRecord, Finding, JsonValue
from portfolio_management.contracts.evidence import ResultState


def arithmetic_context() -> Context:
    """Use 28 significant digits independently of the caller's decimal context.

    Monetary amounts are not rounded to cents. Nonterminating ratios follow
    Decimal's half-even rounding; arithmetic outside this context is rejected.
    """
    return Context(
        prec=28,
        rounding=ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow, Underflow],
    )


def nonnegative_decimal(value: object, *, positive: bool = False) -> bool:
    return (
        isinstance(value, Decimal) and value.is_finite() and (value > 0 if positive else value >= 0)
    )


def positive_count(value: object) -> bool:
    return type(value) is int and value > 0


def valid_currency(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Z]{3}", value) is not None


def requested_value(value: object) -> JsonValue:
    """Describe rejected numeric inputs without putting NaN/Infinity into JSON."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (Decimal, float)):
        return {"type": type(value).__name__, "value": str(value)}
    return {"type": type(value).__name__}


def calculation_record(
    formula: str,
    inputs: dict[str, Decimal],
    parameters: dict[str, JsonValue],
    *,
    outputs: dict[str, Decimal] | None = None,
    units: dict[str, str] | None = None,
    currency: str | None = None,
    status: ResultState = "success",
    findings: tuple[Finding, ...] = (),
    reason: str | None = None,
    input_refs: tuple[str, ...] = (),
    unknown_margin: bool = False,
    unknown_outputs: tuple[str, ...] = (),
) -> CalculationRecord:
    """Bind formula/version, values and assumptions using canonical JSON/SHA-256."""
    outputs = outputs if outputs is not None else {}
    units = units if units is not None else {}
    availability = {key: Availability.KNOWN for key in inputs.keys() | outputs.keys()}
    availability.update({key: Availability.UNKNOWN for key in unknown_outputs})
    if unknown_margin:
        availability["broker_margin"] = Availability.UNKNOWN
    record = CalculationRecord(
        calculation_id="pending-content-hash",
        status=status,
        formula=formula,
        formula_version="1",
        inputs=inputs,
        parameters=parameters | {"decimal_precision": 28, "decimal_rounding": "ROUND_HALF_EVEN"},
        outputs=outputs,
        units=units,
        currencies={
            key: currency
            for key, unit in units.items()
            if currency is not None and unit in {"currency", "currency_per_share"}
        },
        availability=availability,
        input_refs=input_refs,
        findings=findings,
        reason=reason,
    )
    encoded = json.dumps(
        record.model_dump(mode="json", exclude={"calculation_id", "computed_at"}),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return record.model_copy(
        update={"calculation_id": f"calculation:{hashlib.sha256(encoded).hexdigest()}"}
    )
