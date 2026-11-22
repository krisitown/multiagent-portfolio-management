"""Direct typed dispatch to the existing independent Decimal calculators."""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, DecimalException, localcontext
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator

from portfolio_management.config import PolicyConfig
from portfolio_management.contracts import ContractIdentity, PortfolioSnapshot, Position, ToolResult
from portfolio_management.contracts.common import Currency, FinancialDecimal
from portfolio_management.domain.coverage import match_coverage
from portfolio_management.domain.money import arithmetic_context, calculation_record
from portfolio_management.domain.option_math import expiry_payoff, put_economics, roll_economics
from portfolio_management.domain.performance import performance
from portfolio_management.domain.portfolio import analyze_portfolio
from portfolio_management.vault import EvidenceStore, InvalidRecord, RecordNotFound


class FinanceObjectArgument:
    """Advertise an object while deferring shape errors to the safe tool boundary."""

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        from pydantic_core import core_schema

        return core_schema.any_schema()

    @classmethod
    def __get_pydantic_json_schema__(cls, schema: Any, handler: Any) -> dict[str, Any]:
        return {"additionalProperties": True, "type": "object"}


class Parameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _exact_fields(value: Any, fields: tuple[str, ...]) -> None:
    if isinstance(value, dict) and any(
        isinstance(value.get(field), (float, bool)) for field in fields
    ):
        raise ValueError("Use exact decimal text, Decimal or integers")


class PutParameters(Parameters):
    contract: ContractIdentity
    price_basis: Annotated[str, Field(min_length=1)] = "unspecified_input_not_executable"

    @field_validator("contract", mode="before")
    @classmethod
    def exact_contract_terms(cls, value: Any) -> Any:
        _exact_fields(value, ("strike", "multiplier"))
        return value


class RollParameters(Parameters):
    currency: Currency | None = None


class ExpiryParameters(Parameters):
    positions: tuple[Position, ...]

    @field_validator("positions", mode="before")
    @classmethod
    def exact_position_numbers(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            for position in value:
                _exact_fields(position, ("quantity", "encumbered_quantity"))
                if isinstance(position, dict):
                    _exact_fields(position.get("contract"), ("strike", "multiplier"))
                    for field in ("valuation", "cost_basis"):
                        _exact_fields(position.get(field), ("value",))
        return value


class PositionAnalysisParameters(ExpiryParameters):
    base_currency: Currency
    as_of: AwareDatetime


class ArithmeticParameters(Parameters):
    operation: Literal["add", "subtract", "multiply", "divide"]
    result_unit: Annotated[str, Field(min_length=1, max_length=100)] = "unspecified"


class SnapshotParameters(Parameters):
    snapshot_ref: str


class HistoryParameters(Parameters):
    history_refs: tuple[str, ...]


class ExactInputs(Parameters):
    @field_validator("*", mode="before")
    @classmethod
    def exact_numeric(cls, value: Any) -> Any:
        if isinstance(value, (float, bool)):
            raise ValueError("Use exact decimal text, Decimal or integers")
        return value


class PutInputs(ExactInputs):
    quantity: Annotated[FinancialDecimal, Field(gt=0)]
    premium: Annotated[FinancialDecimal, Field(ge=0)]
    fees: Annotated[FinancialDecimal, Field(ge=0)]


class ArithmeticInputs(Parameters):
    values: Annotated[tuple[FinancialDecimal, ...], Field(min_length=2, max_length=32)]

    @field_validator("values", mode="before")
    @classmethod
    def exact_values(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)) and any(
            isinstance(item, (float, bool)) for item in value
        ):
            raise ValueError("Use exact decimal text, Decimal or integers")
        return value


class RollInputs(ExactInputs):
    quantity: Annotated[FinancialDecimal, Field(gt=0)]
    fees: Annotated[FinancialDecimal, Field(ge=0)]
    multiplier: Annotated[FinancialDecimal, Field(gt=0)]
    original_credit: Annotated[FinancialDecimal, Field(ge=0)] | None = None
    close_debit: Annotated[FinancialDecimal, Field(ge=0)]
    new_credit: Annotated[FinancialDecimal, Field(ge=0)]


def _count(quantity: Decimal) -> int:
    if quantity != quantity.to_integral_value():
        raise ValueError(
            "Quantity must be a whole contract count, not underlying shares; "
            "2 contracts with multiplier 100 represent 200 shares"
        )
    return int(quantity)


def finance_tools(
    store: EvidenceStore,
    snapshot: PortfolioSnapshot | None = None,
    policy: PolicyConfig | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    clock = clock or (lambda: datetime.now(UTC))
    snapshot_ref = (
        store.put("snapshot", snapshot.model_dump(mode="json")) if snapshot is not None else None
    )

    def selected(ref: str) -> PortfolioSnapshot:
        if snapshot is None or ref not in {snapshot.snapshot_id, snapshot_ref}:
            raise InvalidRecord("Calculation requires the exact supplied snapshot")
        assert snapshot_ref is not None
        return PortfolioSnapshot.model_validate(store.get(snapshot_ref))

    def historical(ref: str) -> PortfolioSnapshot:
        if snapshot is not None and ref in {snapshot.snapshot_id, snapshot_ref}:
            return selected(ref)
        if not ref.startswith("snapshot:"):
            raise InvalidRecord("History requires immutable run-local snapshot references")
        row = PortfolioSnapshot.model_validate(store.get(ref))
        if snapshot is not None and row.account_alias != snapshot.account_alias:
            raise InvalidRecord("Performance history belongs to another account")
        return row

    async def finance_calculate(
        formula: Literal[
            "put_economics",
            "roll_economics",
            "expiry_payoff",
            "match_coverage",
            "analyze_portfolio",
            "performance",
            "arithmetic",
        ],
        inputs: FinanceObjectArgument,
        parameters: FinanceObjectArgument | None = None,
    ) -> dict[str, Any]:
        """Exact Decimal financial arithmetic; success verifies supplied arithmetic only.

        Use decimal text, e.g. "2.50", never JSON floats/booleans. Positive option
        quantity means whole CONTRACT COUNT in put/roll; Position.quantity is signed:
        negative short, positive long. Option positions count contracts, stock counts
        shares. Multiplier is shares per contract, never a second quantity conversion.
        Example (synthetic arithmetic fixture): 2 contracts * 100 shares/contract =
        200 shares; pass quantity="2", multiplier="100", NOT quantity="200".
        Reconcile returned quantities and units with the source holdings. The tool
        cannot detect that a valid count describes the wrong intended position.

        put_economics: inputs={quantity,premium,fees}; parameters={contract,price_basis}.
        Premium/strike are currency/share; fees total currency (not per contract).
        Contract={instrument_id,instrument_type:"option",currency,underlying_id,
        expiry:"YYYY-MM-DD",option_right:"put",strike,multiplier}; strike/multiplier
        use decimal text. price_basis labels the supplied price field, e.g. "bid";
        omitted basis is unspecified. No price is certified executable.
        Synthetic example: quantity="2",premium="1.25",fees="1",strike="50",
        multiplier="100" -> net premium 249, gross assignment funding 10000.

        roll_economics: inputs={quantity,multiplier,close_debit,new_credit,fees,
        original_credit?}; parameters={currency} optional. Prices are currency/share;
        fees total. Equal-count, equal-multiplier short-option close/reopen only.
        net_roll_cash = quantity*multiplier*(new_credit-close_debit)-fees;
        positive cash is credit, negative cash debit; it is not realized profit.
        original_credit is optional opening premium/share: omit unknown basis,
        never substitute zero/current mark. Missing basis leaves old-leg realization
        and combined-history cash unknown, but incremental roll cash calculable.
        Synthetic example: quantity="2",multiplier="100",close_debit="3.10",
        new_credit="4.20",fees="2" -> net_roll_cash="218" without opening basis.

        expiry_payoff: inputs={underlying_id:spot_decimal_text}; parameters={positions:[
        {position_id,contract,quantity,currency}]}. Stock contract requires
        {instrument_id,instrument_type:"stock",currency}; option contract as above
        with option_right "put" or "call". Stock spot key is instrument_id.
        Returns signed stock value plus option intrinsic; excludes premiums, fees,
        basis and dated mark-to-market. Multiple expiries mean simultaneous intrinsic
        stress only, not a realizable value at one dated expiry.

        match_coverage / analyze_portfolio: inputs={}; parameters EITHER {snapshot_ref}
        using the exact supplied snapshot ID/run-local reference, OR explicit relevant
        terms {positions:[Position,...],base_currency:"USD",as_of:"ISO timezone date"}.
        Explicit terms are a partial analytic subset, not a portfolio JSON transport;
        no complete account totals, NAV or current broker margin are inferred.
        Coverage matches option protection only within same underlying, expiry,
        right, currency, multiplier and deliverable; it does not certify margin.
        For stock coverage supply encumbered_quantity explicitly ("0" only if known).

        arithmetic: inputs={values:[decimal_text,...]} (2..32 operands);
        parameters={operation:"add"|"subtract"|"multiply"|"divide",result_unit?}.
        Operates left to right: divide ["12","3","2"] = 2. Chain calls for
        parentheses; no expression/code evaluation. Result unit is a caller label,
        not dimensional validation; include conversions in operands explicitly.
        Synthetic example: multiply ["2","100","0.90"] = 180 currency.
        Results use 28 significant digits, half-even; no automatic cent rounding.

        performance: inputs={}; parameters={history_refs:[snapshot_ref,...]} using
        explicitly supplied immutable run-local same-account snapshots.
        Missing source, capacity, history or policy data stays unknown.
        """
        try:
            if not isinstance(inputs, dict):
                raise ValueError(
                    "inputs must be an object; arithmetic example: "
                    '{"values":["12","3"]}. Do not pass a list or JSON string'
                )
            if parameters is not None and not isinstance(parameters, dict):
                raise ValueError(
                    "parameters must be an object or null; arithmetic example: "
                    '{"operation":"divide"}. Do not pass a list or JSON string'
                )
            params = {} if parameters is None else parameters
            if formula == "put_economics":
                terms, values = (
                    PutParameters.model_validate(params),
                    PutInputs.model_validate(inputs),
                )
                record = put_economics(
                    terms.contract,
                    _count(values.quantity),
                    values.premium,
                    values.fees,
                    price_basis=terms.price_basis,
                )
            elif formula == "roll_economics":
                roll_terms, roll = (
                    RollParameters.model_validate(params),
                    RollInputs.model_validate(inputs),
                )
                record = roll_economics(
                    _count(roll.quantity),
                    roll.multiplier,
                    roll.original_credit,
                    roll.close_debit,
                    roll.new_credit,
                    roll.fees,
                    currency=roll_terms.currency,
                )
            elif formula == "expiry_payoff":
                positions = ExpiryParameters.model_validate(params)
                if any(isinstance(value, (float, bool)) for value in inputs.values()):
                    raise ValueError("Use exact decimal spot inputs")
                record = expiry_payoff(
                    list(positions.positions),
                    {key: Decimal(str(value)) for key, value in inputs.items()},
                )
            elif formula in {"match_coverage", "analyze_portfolio"}:
                if inputs:
                    raise ValueError("Snapshot formulas take no numeric overrides")
                if "positions" in params:
                    explicit = PositionAnalysisParameters.model_validate(params)
                    current = PortfolioSnapshot(
                        snapshot_id="explicit-position-subset",
                        account_alias="not-an-account",
                        source="caller-supplied-position-terms",
                        as_of=explicit.as_of,
                        fetched_at=clock(),
                        base_currency=explicit.base_currency,
                        completeness="partial",
                        positions=explicit.positions,
                    )
                else:
                    current = selected(SnapshotParameters.model_validate(params).snapshot_ref)
                record = (
                    match_coverage(current)
                    if formula == "match_coverage"
                    else analyze_portfolio(current, policy or PolicyConfig(), now=clock())
                )
                if "positions" in params:
                    record = record.model_copy(
                        update={
                            "parameters": record.parameters
                            | {
                                "input_scope": "explicit_position_subset_not_complete_account",
                                "source_verified": False,
                                "account_totals_and_current_margin": "unknown",
                            }
                        }
                    )
            elif formula == "arithmetic":
                arithmetic = ArithmeticParameters.model_validate(params)
                operands = ArithmeticInputs.model_validate(inputs).values
                with localcontext(arithmetic_context()):
                    total = operands[0]
                    expression = str(total)
                    symbol = {"add": "+", "subtract": "-", "multiply": "*", "divide": "/"}[
                        arithmetic.operation
                    ]
                    for value in operands[1:]:
                        expression = f"({expression} {symbol} {value})"
                        if arithmetic.operation == "add":
                            total += value
                        elif arithmetic.operation == "subtract":
                            total -= value
                        elif arithmetic.operation == "multiply":
                            total *= value
                        else:
                            total /= value
                record = calculation_record(
                    "arithmetic",
                    {f"value_{i}": v for i, v in enumerate(operands)},
                    {
                        "operation": arithmetic.operation,
                        "equation": "left fold of supplied values in order",
                        "evaluated_expression": expression,
                        "verification_scope": "supplied_arithmetic_only",
                        "source_verified": False,
                        "unit_status": "caller_label_only_no_dimensional_validation",
                    },
                    outputs={"result": total},
                    units={"result": arithmetic.result_unit},
                )
            elif formula == "performance":
                if inputs:
                    raise ValueError("Performance takes no numeric overrides")
                history = HistoryParameters.model_validate(params)
                record = performance([historical(ref) for ref in history.history_refs])
            else:
                raise ValueError("Unknown deterministic formula")
            payload = record.model_copy(update={"computed_at": clock()}).model_dump(mode="json")
            # Pure IDs describe economics; dated records have a separate immutable identity.
            payload.pop("calculation_id")
            digest = hashlib.sha256(
                json.dumps(
                    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest()
            payload["calculation_id"] = "calculation:" + digest
            ref = store.put("calculation", payload)
            return ToolResult(
                status=record.status,
                data=store.get(ref),
                reason=record.reason,
                findings=record.findings,
                result_refs=(ref,),
            ).model_dump(mode="json")
        except (
            ValidationError,
            ValueError,
            TypeError,
            DecimalException,
            InvalidRecord,
            RecordNotFound,
        ) as exc:
            if isinstance(exc, ValidationError):
                detail = "; ".join(
                    ".".join(str(part) for part in error["loc"]) + ": " + error["type"]
                    for error in exc.errors(include_input=False, include_context=False)
                )
            elif isinstance(exc, DecimalException):
                detail = "Invalid Decimal arithmetic (check zero divisor and finite values)"
            else:
                detail = str(exc)
            return ToolResult(
                status="invalid_data",
                reason=(
                    f"Invalid calculation request: {detail}. Supported formulas: put_economics, "
                    "roll_economics, expiry_payoff, match_coverage, analyze_portfolio, "
                    "performance, arithmetic. "
                    "Use decimal text (no JSON floats/booleans), the exact inputs/parameters "
                    "shapes in the tool description, and supplied snapshot references."
                ),
            ).model_dump(mode="json")

    return {"finance_calculate": finance_calculate}
