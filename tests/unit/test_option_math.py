"""Independent cash-flow expectations for the real deterministic calculators.

Expectations are literal synthetic examples, derived before implementation.
The fixture explanations intentionally use contractual cash flows rather than
calling calculator helpers to construct an oracle.
"""

import importlib
import json
from datetime import date
from decimal import ROUND_UP, Decimal, DefaultContext, localcontext
from pathlib import Path

import pytest

from wheel_team.contracts import CalculationRecord, ContractIdentity, Position, QuantityValue

EXPECTED = json.loads(
    (Path(__file__).parents[1] / "fixtures/finance/option_expected.json").read_text()
)
D = Decimal


def api():
    # Runtime import lets pytest collect every behavior before the module exists.
    try:
        return importlib.import_module("wheel_team.domain.option_math")
    except ModuleNotFoundError as exc:
        if exc.name not in {"wheel_team.domain", "wheel_team.domain.option_math"}:
            raise
        pytest.fail("missing option economics implementation: wheel_team.domain.option_math")


def option(*, right="put", strike="50", multiplier="100", currency="USD", **overrides):
    payload = {
        "instrument_id": f"SYN-{right}-{strike}",
        "instrument_type": "option",
        "underlying_id": "SYN",
        "expiry": date(2026, 12, 18),
        "option_right": right,
        "strike": D(strike),
        "multiplier": D(multiplier),
        "currency": currency,
    }
    return ContractIdentity(**(payload | overrides))


def position(identity, quantity, position_id="position-syn", **overrides):
    return Position(
        position_id=position_id,
        contract=identity,
        quantity=D(quantity),
        currency=identity.currency,
        **overrides,
    )


def assert_known_amount(record, key, expected, currency, unit="currency"):
    assert isinstance(record, CalculationRecord)
    assert record.outputs[key] == D(expected)
    assert isinstance(record.outputs[key], Decimal)
    assert record.availability[key] == "known"
    assert record.currencies[key] == currency
    assert record.units[key] == unit


def assert_rejected(record, status="invalid_data"):
    assert isinstance(record, CalculationRecord)
    assert record.status == status
    assert record.outputs == {}
    assert record.findings
    assert all(finding.reason for finding in record.findings)
    assert record.reason


@pytest.mark.parametrize("case", EXPECTED["put_cases"], ids=lambda case: case["id"])
def test_put_economics_matches_independent_cash_flows(case):
    # Catches hard-coded 100 multipliers, repeated fees and sign/denominator errors.
    values = case["inputs"]
    record = api().put_economics(
        option(
            strike=values["strike"],
            multiplier=values["multiplier"],
            currency=values["currency"],
        ),
        values["quantity"],
        D(values["premium"]),
        D(values["fees"]),
    )
    assert record.status == "success"
    for key, expected in case["expected"].items():
        if key == "return_on_strike_notional":
            assert record.outputs[key] == D(expected)
            assert record.units[key] == "ratio"
            assert record.availability[key] == "known"
            assert key not in record.currencies
        else:
            unit = (
                "currency_per_share"
                if key in {"net_premium_per_share", "breakeven"}
                else "currency"
            )
            assert_known_amount(record, key, expected, values["currency"], unit)
    assert record.parameters["return_denominator"] == "strike_notional"
    assert record.parameters["annualization"] == "not_computed_no_term_input"
    assert record.parameters["price_basis"] == "unspecified_input_not_executable"
    assert record.availability["broker_margin"] == "unknown"
    assert "broker_margin" not in record.outputs
    assert record.formula and record.formula_version
    assert record.inputs["fees"] == D(values["fees"])


@pytest.mark.parametrize("basis", ["midpoint", "caller_labelled_executable_price"])
def test_price_basis_survives_without_certifying_execution(basis):
    record = api().put_economics(option(), 1, D("2"), D("0"), price_basis=basis)
    assert record.status == "success"
    assert record.parameters["price_basis"] == basis
    assert record.parameters["execution_certified"] is False
    assert not any("annualized" in key for key in record.outputs)


@pytest.mark.parametrize("quantity", [0, -1, True, D("1.5"), 1.0])
def test_put_rejects_nonpositive_or_noninteger_contract_counts(quantity):
    assert_rejected(api().put_economics(option(), quantity, D("2"), D("0")))


@pytest.mark.parametrize(
    "premium, fees",
    [
        (D("-1"), D("0")),
        (D("1"), D("-1")),
        (D("NaN"), D("0")),
        (D("1"), D("Infinity")),
        (1.25, D("0")),
    ],
)
def test_put_rejects_negative_nonfinite_or_binary_float_cash_inputs(premium, fees):
    assert_rejected(api().put_economics(option(), 1, premium, fees))


def test_zero_premium_and_fees_are_known_zero_values():
    record = api().put_economics(option(), 1, D("0"), D("0"))
    assert record.status == "success"
    assert_known_amount(record, "net_premium", "0", "USD")
    assert_known_amount(record, "breakeven", "50", "USD", "currency_per_share")


def test_zero_strike_does_not_fabricate_return_or_divide_by_zero():
    record = api().put_economics(option(strike="0"), 1, D("0"), D("0"))
    assert_rejected(record)


@pytest.mark.parametrize(
    "changes",
    [
        {"deliverable": {"shares": "125", "cash": "12.50"}},
        {"deliverable": {"cash": "5000", "currency": "USD"}},
        {"adjustment": "OCC synthetic adjustment"},
    ],
)
def test_adjusted_deliverables_are_explicitly_unsupported_without_guessed_funding(changes):
    identity = option(**changes)
    record = api().put_economics(identity, 2, D("2"), D("0"))
    assert_rejected(record, "unavailable")
    assert any("unsupported" in finding.code for finding in record.findings)
    assert any(identity.instrument_id in finding.affected_ids for finding in record.findings)


def test_call_cannot_be_mislabelled_as_short_put_economics():
    assert_rejected(api().put_economics(option(right="call"), 1, D("2"), D("0")))


@pytest.mark.parametrize("case", EXPECTED["roll_cases"], ids=lambda case: case["id"])
def test_roll_keeps_realized_loss_new_credit_cash_and_history_separate(case):
    # A credit roll must not erase the loss or count original premium twice.
    values = case["inputs"]
    record = api().roll_economics(
        values["quantity"],
        *[
            D(values[key])
            for key in ("multiplier", "original_credit", "close_debit", "new_credit", "fees")
        ],
        currency="EUR",
    )
    assert record.status == "success"
    for key, expected in case["expected"].items():
        unit = "currency_per_share" if key.endswith("per_share") else "currency"
        assert_known_amount(record, key, expected, "EUR", unit)
    assert record.parameters["per_share_cash_excludes_fees"] is True
    assert record.parameters["leg_fee_allocation"] == "not_allocated"
    assert record.parameters["common_contract_currency_assumed"] is True
    assert record.formula and record.formula_version


def test_roll_six_positional_inputs_never_invent_currency():
    record = api().roll_economics(2, D("100"), D("2"), D("5"), D("6"), D("4"))
    assert record.outputs["old_leg_realized_per_share"] == D("-3")
    assert record.outputs["net_roll_cash_per_share"] == D("1")
    assert record.outputs["net_roll_cash"] == D("196")
    assert record.currencies == {}
    assert record.parameters["currency_status"] == "unknown"
    assert record.availability["net_roll_cash"] == "known"
    assert record.reason
    assert any(finding.severity == "warning" for finding in record.findings)


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity": 0},
        {"quantity": -1},
        {"quantity": True},
        {"quantity": D("0.5")},
        {"multiplier": D("0")},
        {"multiplier": D("-10")},
        {"original_credit": D("-1")},
        {"close_debit": D("-1")},
        {"new_credit": D("NaN")},
        {"fees": D("-1")},
        {"currency": "usd"},
        {"multiplier": 100.0},
    ],
)
def test_roll_rejects_invalid_dimensions_without_partial_totals(changes):
    values = dict(
        quantity=1,
        multiplier=D("100"),
        original_credit=D("2"),
        close_debit=D("5"),
        new_credit=D("6"),
        fees=D("0"),
        currency="USD",
    )
    assert_rejected(api().roll_economics(**(values | changes)))


def test_expiry_payoff_respects_signed_quantities_and_partial_protection():
    rows = [
        position(option(), "-2", "short-puts"),
        position(option(strike="45"), "1", "long-put"),
        position(
            ContractIdentity(instrument_id="SYN", instrument_type="stock", currency="USD"),
            "10",
            "stock",
            cost_basis=QuantityValue(
                availability="known", value=D("999"), unit="currency", currency="USD"
            ),
        ),
    ]
    record = api().expiry_payoff(rows, {"SYN": D("40")})
    assert record.status == "success"
    assert_known_amount(
        record, "expiry_payoff", EXPECTED["expiry_cases"][0]["expected_payoff"], "USD"
    )
    assert record.parameters["includes_premiums_fees_or_cost_basis"] is False
    assert record.parameters["scenario_type"] == "expiry_payoff_not_mark_to_market"
    assert record.availability["broker_margin"] == "unknown"
    assert "broker_margin" not in record.outputs
    assert set(record.input_refs) == {"short-puts", "long-put", "stock"}


def test_expiry_call_payoff_uses_call_intrinsic_value():
    rows = [
        position(option(right="call", strike="30"), "2", "long-calls"),
        position(option(right="call", strike="35"), "-1", "short-call"),
    ]
    record = api().expiry_payoff(rows, {"SYN": D("40")})
    assert_known_amount(
        record, "expiry_payoff", EXPECTED["expiry_cases"][1]["expected_payoff"], "USD"
    )


def test_out_of_money_options_have_known_zero_payoff():
    rows = [position(option(), "-2"), position(option(strike="45"), "1", "long-put")]
    record = api().expiry_payoff(rows, {"SYN": D("55")})
    assert_known_amount(
        record, "expiry_payoff", EXPECTED["expiry_cases"][2]["expected_payoff"], "USD"
    )


def test_expiry_payoff_uses_contract_multiplier_instead_of_one_hundred():
    record = api().expiry_payoff([position(option(multiplier="10"), "-3")], {"SYN": D("40")})
    assert_known_amount(record, "expiry_payoff", "-300", "USD")


def test_expiry_zero_spot_is_valid_and_not_treated_as_missing():
    record = api().expiry_payoff([position(option(), "-2")], {"SYN": D("0")})
    assert_known_amount(record, "expiry_payoff", "-10000", "USD")


def test_expiry_missing_spot_blocks_aggregate_instead_of_using_zero():
    assert_rejected(api().expiry_payoff([position(option(), "-2")], {}), "unavailable")


@pytest.mark.parametrize("spot", [D("-1"), D("NaN"), D("Infinity"), 40.0])
def test_expiry_rejects_invalid_or_binary_float_spots(spot):
    assert_rejected(api().expiry_payoff([position(option(), "-2")], {"SYN": spot}))


@pytest.mark.parametrize("quantity", ["0", "-0.5"])
def test_expiry_rejects_zero_and_fractional_option_contract_counts(quantity):
    assert_rejected(api().expiry_payoff([position(option(), quantity)], {"SYN": D("40")}))


def test_expiry_mixed_currencies_never_add_without_fx():
    rows = [
        position(option(), "-1", "usd-put"),
        position(option(currency="EUR", instrument_id="SYN-EUR-PUT"), "1", "eur-put"),
    ]
    assert_rejected(api().expiry_payoff(rows, {"SYN": D("40")}))


def test_expiry_adjusted_cash_deliverable_never_receives_plain_vanilla_payoff():
    identity = option(deliverable={"shares": "125", "cash": "12.50"})
    assert_rejected(
        api().expiry_payoff([position(identity, "-1")], {"SYN": D("40")}), "unavailable"
    )


def test_expiry_unsupported_instrument_is_visible_as_finding():
    identity = ContractIdentity(instrument_id="SYN-BOND", instrument_type="bond", currency="USD")
    record = api().expiry_payoff([position(identity, "1", "bond")], {"SYN-BOND": D("100")})
    assert_rejected(record, "unavailable")
    assert any("bond" in finding.affected_ids for finding in record.findings)


def test_financial_outputs_serialize_as_decimal_strings():
    record = api().put_economics(option(), 2, D("2"), D("3"))
    encoded = json.loads(record.model_dump_json())
    assert encoded["outputs"]["gross_assignment_funding"] == "10000"
    assert encoded["outputs"]["net_premium"] == "397"
    assert encoded["outputs"]["breakeven"] == "48.015"


def test_calculation_identity_binds_inputs_currency_contract_and_price_basis():
    calculator = api()
    base = calculator.put_economics(option(), 2, D("2"), D("3"))
    repeated = calculator.put_economics(option(), 2, D("2"), D("3"))
    alternatives = [
        calculator.put_economics(option(), 1, D("2"), D("3")),
        calculator.put_economics(option(currency="EUR"), 2, D("2"), D("3")),
        calculator.put_economics(option(strike="51"), 2, D("2"), D("3")),
        calculator.put_economics(option(), 2, D("2"), D("3"), price_basis="midpoint"),
    ]
    assert repeated == base
    assert base.computed_at is None
    assert all(record.calculation_id != base.calculation_id for record in alternatives)
    unspecified = calculator.roll_economics(1, D("100"), D("2"), D("5"), D("6"), D("0"))
    labelled = calculator.roll_economics(
        1, D("100"), D("2"), D("5"), D("6"), D("0"), currency="EUR"
    )
    assert unspecified.calculation_id != labelled.calculation_id


def test_expiry_identity_binds_position_terms_and_spot_not_only_reference_ids():
    calculator = api()
    base = calculator.expiry_payoff([position(option(), "-1")], {"SYN": D("40")})
    changed_terms = calculator.expiry_payoff(
        [position(option(strike="51"), "-1")], {"SYN": D("40")}
    )
    changed_spot = calculator.expiry_payoff([position(option(), "-1")], {"SYN": D("41")})
    assert (
        len({base.calculation_id, changed_terms.calculation_id, changed_spot.calculation_id}) == 3
    )


def test_decimal_calculations_do_not_depend_on_callers_decimal_context():
    calculator = api()
    with localcontext() as context:
        context.prec = 4
        low = calculator.put_economics(option(), 2, D("2"), D("3"))
    with localcontext() as context:
        context.prec = 40
        high = calculator.put_economics(option(), 2, D("2"), D("3"))
    assert low == high
    assert low.outputs["breakeven"] == D("48.015")


def test_decimal_rounding_does_not_inherit_mutable_process_defaults(monkeypatch):
    calculator = api()
    base = calculator.put_economics(option(), 3, D("2"), D("1"))
    monkeypatch.setattr(DefaultContext, "rounding", ROUND_UP)
    changed_default = calculator.put_economics(option(), 3, D("2"), D("1"))
    assert base == changed_default
    assert base.outputs["breakeven"] == D("48.00333333333333333333333333")
