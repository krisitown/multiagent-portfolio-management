"""Plain-vanilla option cash flows, separate from broker funding capacity.

All prices are illustrative per-share inputs. Fees are total amounts in the
same contract currency. Nothing here obtains or certifies an executable quote.
"""

from decimal import Decimal, DecimalException, localcontext

from portfolio_management.contracts import (
    CalculationRecord,
    ContractIdentity,
    Finding,
    JsonValue,
    Position,
)
from portfolio_management.contracts.evidence import ResultState
from portfolio_management.domain.money import (
    arithmetic_context,
    calculation_record,
    nonnegative_decimal,
    positive_count,
    requested_value,
    valid_currency,
)

ZERO = Decimal("0")


def _terms(contract: ContractIdentity) -> dict[str, JsonValue]:
    # Economic identity is explicit; provider raw payloads are not calculation inputs.
    return contract.model_dump(mode="json", exclude={"raw"})


def _unsupported(contract: ContractIdentity) -> bool:
    return bool(contract.deliverable or contract.adjustment)


def _rejected(
    formula: str,
    parameters: dict[str, JsonValue],
    reason: str,
    *,
    status: ResultState = "invalid_data",
    code: str = "invalid_calculation_input",
    refs: tuple[str, ...] = (),
) -> CalculationRecord:
    return calculation_record(
        formula,
        {},
        parameters,
        status=status,
        reason=reason,
        input_refs=refs,
        findings=(Finding(code=code, severity="error", affected_ids=refs, reason=reason),),
    )


def put_economics(
    contract: ContractIdentity,
    quantity: int,
    premium: Decimal,
    fees: Decimal,
    *,
    price_basis: str = "unspecified_input_not_executable",
) -> CalculationRecord:
    """Illustrate selling positive whole contracts of a plain-vanilla put.

    Maximum loss is the loss at a zero underlying price, net of premium/fees.
    Assignment funding remains gross strike notional, irrespective of premium.
    """
    formula = "put_economics"
    parameters: dict[str, JsonValue] = {
        "contract": _terms(contract),
        "requested_quantity": requested_value(quantity),
        "quantity_convention": "positive_whole_contracts_not_share_equivalent",
        "holdings_verified": False,
        "requested_premium": requested_value(premium),
        "requested_fees": requested_value(fees),
        "price_basis": requested_value(price_basis),
        "execution_certified": False,
        "return_denominator": "strike_notional",
        "annualization": "not_computed_no_term_input",
        "fee_currency_assumed": contract.currency,
        "equations": {
            "gross_assignment_funding": "quantity * multiplier * strike",
            "gross_premium": "quantity * multiplier * premium",
            "net_premium": "gross_premium - fees",
            "net_premium_per_share": "net_premium / (quantity * multiplier)",
            "breakeven": "strike - net_premium_per_share",
            "maximum_loss": "gross_assignment_funding - net_premium",
            "return_on_strike_notional": "net_premium / gross_assignment_funding",
        },
    }
    refs = (contract.instrument_id,)
    if contract.instrument_type != "option" or contract.option_right != "put":
        return _rejected(formula, parameters, "Short-put economics require a put option", refs=refs)
    if _unsupported(contract):
        return _rejected(
            formula,
            parameters,
            "Adjusted or explicit deliverables are not supported",
            status="unavailable",
            code="unsupported_deliverable",
            refs=refs,
        )
    if (
        not positive_count(quantity)
        or not nonnegative_decimal(contract.strike, positive=True)
        or not nonnegative_decimal(contract.multiplier, positive=True)
        or not nonnegative_decimal(premium)
        or not nonnegative_decimal(fees)
        or not isinstance(price_basis, str)
        or not price_basis.strip()
    ):
        return _rejected(
            formula,
            parameters,
            "Require positive integer count, positive strike/multiplier, finite nonnegative "
            "Decimal prices/fees and a nonempty price basis",
            refs=refs,
        )
    assert contract.strike is not None and contract.multiplier is not None
    inputs = {
        "quantity": Decimal(quantity),
        "strike": contract.strike,
        "multiplier": contract.multiplier,
        "premium": premium,
        "fees": fees,
    }
    try:
        with localcontext(arithmetic_context()):
            shares = Decimal(quantity) * contract.multiplier
            funding = shares * contract.strike
            gross = shares * premium
            net = gross - fees
            net_per_share = net / shares
            outputs = {
                "gross_assignment_funding": funding,
                "gross_premium": gross,
                "net_premium": net,
                "net_premium_per_share": net_per_share,
                "breakeven": contract.strike - net_per_share,
                "maximum_loss": funding - net,
                "return_on_strike_notional": net / funding,
            }
    except DecimalException:
        return _rejected(
            formula, parameters, "Inputs exceed supported Decimal arithmetic", refs=refs
        )
    units = {
        "quantity": "contracts",
        "multiplier": "shares_per_contract",
        "strike": "currency_per_share",
        "premium": "currency_per_share",
        "fees": "currency",
        **{key: "currency" for key in outputs},
        "net_premium_per_share": "currency_per_share",
        "breakeven": "currency_per_share",
        "return_on_strike_notional": "ratio",
    }
    return calculation_record(
        formula,
        inputs,
        parameters,
        outputs=outputs,
        units=units,
        currency=contract.currency,
        input_refs=refs,
        unknown_margin=True,
    )


def roll_economics(
    quantity: int,
    multiplier: Decimal,
    original_credit: Decimal | None,
    close_debit: Decimal,
    new_credit: Decimal,
    fees: Decimal,
    *,
    currency: str | None = None,
) -> CalculationRecord:
    """Separate old-leg realization, new credit, roll cash and combined history.

    Per-share decomposition is before fees: the total fee input cannot allocate
    fees to individual legs. A None opening credit leaves historical realization
    and combined-history cash unknown while preserving incremental roll cash.
    Unknown denomination permits abstract arithmetic
    with an explicit warning, without inventing a currency.
    """
    formula = "roll_economics"
    amounts = {
        "multiplier": multiplier,
        "original_credit": original_credit,
        "close_debit": close_debit,
        "new_credit": new_credit,
        "fees": fees,
    }
    parameters: dict[str, JsonValue] = {
        "requested_quantity": requested_value(quantity),
        "quantity_convention": "positive_whole_contracts_not_share_equivalent",
        "holdings_verified": False,
        "requested_amounts": {key: requested_value(value) for key, value in amounts.items()},
        "currency": requested_value(currency),
        "currency_status": "unknown" if currency is None else "known",
        "common_contract_currency_assumed": True,
        "historical_basis_status": "unknown" if original_credit is None else "supplied",
        "per_share_cash_excludes_fees": True,
        "leg_fee_allocation": "not_allocated",
        "new_leg_risk": "not_computed_no_contract_terms",
        "equations": {
            "old_leg_realized_per_share": "original_credit - close_debit",
            "old_leg_realized_total": "quantity * multiplier * old_leg_realized_per_share",
            "new_leg_credit_total": "quantity * multiplier * new_credit",
            "net_roll_cash_per_share": "new_credit - close_debit",
            "net_roll_cash": "quantity * multiplier * net_roll_cash_per_share - fees",
            "combined_history_cash": "quantity * multiplier * original_credit + net_roll_cash",
        },
    }
    if (
        not positive_count(quantity)
        or not nonnegative_decimal(multiplier, positive=True)
        or not all(
            nonnegative_decimal(value)
            for key, value in amounts.items()
            if key != "multiplier" and not (key == "original_credit" and value is None)
        )
        or (currency is not None and not valid_currency(currency))
    ):
        return _rejected(
            formula,
            parameters,
            "Require positive integer count/multiplier, finite nonnegative Decimal "
            "prices/fees and optional uppercase three-letter contract currency",
        )
    inputs = {
        "quantity": Decimal(quantity),
        **{key: value for key, value in amounts.items() if value is not None},
    }
    try:
        with localcontext(arithmetic_context()):
            shares = Decimal(quantity) * multiplier
            roll_cash_per_share = new_credit - close_debit
            roll_cash = shares * roll_cash_per_share - fees
            outputs = {
                "new_leg_credit_total": shares * new_credit,
                "net_roll_cash_per_share": roll_cash_per_share,
                "net_roll_cash": roll_cash,
            }
            if original_credit is not None:
                realized = original_credit - close_debit
                outputs.update(
                    {
                        "old_leg_realized_per_share": realized,
                        "old_leg_realized_total": shares * realized,
                        "combined_history_cash": shares * original_credit + roll_cash,
                    }
                )
    except DecimalException:
        return _rejected(formula, parameters, "Inputs exceed supported Decimal arithmetic")
    units = {
        "quantity": "contracts",
        "multiplier": "shares_per_contract",
        "fees": "currency",
        **{key: "currency_per_share" for key in ("original_credit", "close_debit", "new_credit")},
        **{
            key: "currency_per_share" if key.endswith("per_share") else "currency"
            for key in outputs
        },
    }
    reason = "Arithmetic only; currency unspecified" if currency is None else None
    findings = (
        (Finding(code="currency_unspecified", severity="warning", reason=reason),)
        if reason is not None
        else ()
    )
    return calculation_record(
        formula,
        inputs,
        parameters,
        outputs=outputs,
        units=units,
        currency=currency,
        findings=findings,
        reason=reason,
        unknown_margin=True,
        unknown_outputs=(
            ("old_leg_realized_per_share", "old_leg_realized_total", "combined_history_cash")
            if original_credit is None
            else ()
        ),
    )


def expiry_payoff(positions: list[Position], spots: dict[str, Decimal]) -> CalculationRecord:
    """Signed terminal stock/intrinsic option value under supplied spot scenarios.

    Spot keys are option underlying IDs or stock instrument IDs. This excludes
    premiums, fees and cost basis and does not project dated portfolio NAV.
    """
    formula = "expiry_payoff"
    expiries = sorted(
        {str(p.contract.expiry) for p in positions if p.contract.instrument_type == "option"}
    )
    refs = tuple(position.position_id for position in positions)
    parameters: dict[str, JsonValue] = {
        "positions": [
            {
                "position_id": position.position_id,
                "contract": _terms(position.contract),
                "quantity": str(position.quantity),
                "currency": position.currency,
            }
            for position in positions
        ],
        "requested_spots": {key: requested_value(value) for key, value in spots.items()},
        "includes_premiums_fees_or_cost_basis": False,
        "scenario_type": "expiry_payoff_not_mark_to_market",
        "option_expiries": [str(expiry) for expiry in expiries],
        "mixed_expiry_interpretation": (
            "simultaneous_intrinsic_stress_not_one_dated_expiry_or_mark_to_market"
            if len(expiries) > 1
            else "single_expiry_intrinsic_value"
        ),
        "quantity_convention": "signed_contracts_for_options_signed_shares_for_stock",
        "holdings_verified": False,
        "spot_key": "option_underlying_id_or_stock_instrument_id",
        "equations": {
            "stock_value": "signed_quantity * spot",
            "put_value": "signed_quantity * multiplier * max(strike - spot, 0)",
            "call_value": "signed_quantity * multiplier * max(spot - strike, 0)",
            "expiry_payoff": "sum(supported_position_terminal_values)",
        },
    }
    if not positions:
        return _rejected(
            formula,
            parameters,
            "No positions or contract currency supplied",
            status="unavailable",
            code="positions_unavailable",
        )
    if len(set(refs)) != len(refs):
        return _rejected(
            formula, parameters, "Duplicate position IDs would double count", refs=refs
        )
    currencies = {position.currency for position in positions}
    if len(currencies) != 1:
        return _rejected(
            formula,
            parameters,
            "Mixed currencies require explicit FX before aggregation",
            refs=refs,
        )
    for position in positions:
        contract = position.contract
        if contract.instrument_type not in {"stock", "option"} or _unsupported(contract):
            return _rejected(
                formula,
                parameters,
                "Instrument or explicit/adjusted deliverable is unsupported",
                status="unavailable",
                code="unsupported_instrument_or_deliverable",
                refs=(position.position_id, contract.instrument_id),
            )
        if (
            not isinstance(position.quantity, Decimal)
            or not position.quantity.is_finite()
            or position.quantity == 0
            or (
                contract.instrument_type == "option"
                and (
                    position.quantity != position.quantity.to_integral_value()
                    or not nonnegative_decimal(contract.multiplier, positive=True)
                    or not nonnegative_decimal(contract.strike)
                    or contract.option_right not in {"put", "call"}
                )
            )
        ):
            return _rejected(
                formula,
                parameters,
                "Require nonzero finite quantity and whole option contracts",
                refs=(position.position_id,),
            )
        key = (
            contract.underlying_id
            if contract.instrument_type == "option"
            else contract.instrument_id
        )
        if key not in spots:
            return _rejected(
                formula,
                parameters,
                "Required underlying spot is missing",
                status="unavailable",
                code="spot_unavailable",
                refs=(position.position_id,),
            )
        if not nonnegative_decimal(spots[key]):
            return _rejected(
                formula,
                parameters,
                "Spot must be a finite nonnegative Decimal",
                refs=(position.position_id,),
            )
    inputs: dict[str, Decimal] = {}
    units: dict[str, str] = {"expiry_payoff": "currency"}
    try:
        with localcontext(arithmetic_context()):
            total = ZERO
            for index, position in enumerate(positions):
                contract = position.contract
                key = (
                    contract.underlying_id
                    if contract.instrument_type == "option"
                    else contract.instrument_id
                )
                assert key is not None
                spot = spots[key]
                inputs[f"position_{index}_quantity"] = position.quantity
                inputs[f"position_{index}_spot"] = spot
                units[f"position_{index}_quantity"] = (
                    "contracts" if contract.instrument_type == "option" else "shares"
                )
                units[f"position_{index}_spot"] = "currency_per_share"
                if contract.instrument_type == "stock":
                    total += position.quantity * spot
                else:
                    assert contract.strike is not None and contract.multiplier is not None
                    inputs[f"position_{index}_strike"] = contract.strike
                    inputs[f"position_{index}_multiplier"] = contract.multiplier
                    inputs[f"position_{index}_share_equivalent_quantity"] = (
                        position.quantity * contract.multiplier
                    )
                    units[f"position_{index}_share_equivalent_quantity"] = "shares"
                    units[f"position_{index}_strike"] = "currency_per_share"
                    units[f"position_{index}_multiplier"] = "shares_per_contract"
                    intrinsic = (
                        contract.strike - spot
                        if contract.option_right == "put"
                        else spot - contract.strike
                    )
                    total += position.quantity * contract.multiplier * max(intrinsic, ZERO)
    except DecimalException:
        return _rejected(
            formula, parameters, "Inputs exceed supported Decimal arithmetic", refs=refs
        )
    return calculation_record(
        formula,
        inputs,
        parameters,
        outputs={"expiry_payoff": total},
        units=units,
        currency=next(iter(currencies)),
        input_refs=refs,
        unknown_margin=True,
    )
