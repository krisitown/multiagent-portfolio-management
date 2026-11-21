"""Exposure and supported performance use explicit synthetic evidence and policy."""

import importlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from wheel_team.contracts import (
    BrokerCapacity,
    CalculationRecord,
    CashBalance,
    CashFlow,
    ContractIdentity,
    PortfolioSnapshot,
    Position,
    QuantityValue,
)

D = Decimal
NOW = datetime(2026, 10, 30, 20, tzinfo=UTC)


def api(module):
    qualified = f"wheel_team.domain.{module}"
    try:
        return importlib.import_module(qualified)
    except ModuleNotFoundError as exc:
        if exc.name not in {"wheel_team.domain", qualified}:
            raise
        pytest.fail(f"missing portfolio {module} implementation")


def policy(**changes):
    module = importlib.import_module("wheel_team.config")
    if not hasattr(module, "PolicyConfig"):
        pytest.fail("missing explicit portfolio PolicyConfig")
    return module.PolicyConfig(**changes)


def amount(value, currency="USD", unit="currency"):
    return QuantityValue(
        availability="known", value=D(value), unit=unit, currency=currency, source="synthetic-host"
    )


def snapshot(**changes):
    values = dict(
        snapshot_id="snapshot-synthetic",
        account_alias="invented-account",
        source="synthetic-host",
        as_of=NOW,
        fetched_at=NOW,
        base_currency="USD",
        completeness="complete",
        positions=(),
        cash=(CashBalance(currency="USD", availability="known", amount=D("10000")),),
        flows=(),
    )
    return PortfolioSnapshot(**(values | changes))


def stock(value="1000", currency="USD", instrument_type="stock"):
    return Position(
        position_id="alpha-holding",
        contract=ContractIdentity(
            instrument_id="issuer-alpha", instrument_type=instrument_type, currency=currency
        ),
        quantity=D("10"),
        currency=currency,
        valuation=amount(value, currency),
        encumbered_quantity=D("0"),
    )


def put(identifier="shorts", quantity="-2", strike="50", currency="USD"):
    return Position(
        position_id=identifier,
        contract=ContractIdentity(
            instrument_id=f"ALPHA-{strike}-{currency}",
            instrument_type="option",
            underlying_id="issuer-alpha",
            expiry=date(2026, 12, 18),
            option_right="put",
            strike=D(strike),
            multiplier=D("100"),
            currency=currency,
        ),
        quantity=D(quantity),
        currency=currency,
        valuation=amount("0", currency),
    )


def assert_money(record, key, expected, currency="USD"):
    assert isinstance(record, CalculationRecord)
    assert record.outputs[key] == D(expected)
    assert record.availability[key] == "known"
    assert record.units[key] == "currency"
    assert record.currencies[key] == currency


def test_hedge_does_not_net_away_gross_assignment_or_invent_buying_power():
    result = api("portfolio").analyze_portfolio(
        snapshot(positions=(put(), put("long", "1", "45"))), policy(), now=NOW
    )
    assert_money(result, "gross_assignment_funding", "10000")
    assert result.availability["broker_buying_power"] == "unknown"
    assert "broker_buying_power" not in result.outputs
    assert result.parameters["capacity_readiness"] == "unknown"
    assert result.input_refs
    assert result.formula and result.formula_version


def test_marked_option_value_does_not_erase_separate_issuer_assignment_obligation():
    result = api("portfolio").analyze_portfolio(
        snapshot(positions=(put(),)),
        policy(),
        now=NOW,
    )
    assert result.outputs["largest_issuer_weight"] == D("0")
    assert result.parameters["concentration_method"] == (
        "gross_absolute_marked_issuer_value_divided_by_positive_nav"
    )
    assert result.parameters["gross_assignment_funding_by_issuer_and_currency"] == {
        "issuer-alpha": {"USD": "10000"}
    }
    assert_money(result, "gross_assignment_funding", "10000")
    assert result.parameters["capacity_readiness"] == "unknown"


def test_unset_risk_thresholds_are_not_configured_rather_than_passed():
    result = api("portfolio").analyze_portfolio(snapshot(positions=(stock(),)), policy(), now=NOW)
    checks = result.parameters["policy_checks"]
    assert checks["max_issuer_weight"] == "not_configured"
    assert checks["max_gross_assignment_to_nav"] == "not_configured"
    assert checks["max_snapshot_age_seconds"] == "not_configured"
    assert checks["max_capacity_age_seconds"] == "not_configured"
    assert result.parameters["capacity_readiness"] == "unknown"


@pytest.mark.parametrize("limit, expected", [("0.49", "fail"), ("0.50", "pass"), ("0.51", "pass")])
def test_configured_concentration_uses_nav_denominator_and_inclusive_limit(limit, expected):
    result = api("portfolio").analyze_portfolio(
        snapshot(
            positions=(stock(),),
            cash=(CashBalance(currency="USD", availability="known", amount=D("1000")),),
        ),
        policy(max_issuer_weight=D(limit)),
        now=NOW,
    )
    assert_money(result, "nav", "2000")
    assert result.outputs["largest_issuer_weight"] == D("0.5")
    assert result.units["largest_issuer_weight"] == "ratio"
    assert result.parameters["policy_checks"]["max_issuer_weight"] == expected


def test_owner_can_configure_gross_issuer_limit_above_one_for_a_leveraged_account():
    result = api("portfolio").analyze_portfolio(
        snapshot(positions=(stock("12000"),), valuations=(amount("10000", unit="nav"),)),
        policy(max_issuer_weight=D("1.5")),
        now=NOW,
    )
    assert result.outputs["largest_issuer_weight"] == D("1.2")
    assert result.parameters["policy_checks"]["max_issuer_weight"] == "pass"


def test_missing_cash_does_not_become_zero_in_nav_or_policy_denominator():
    result = api("portfolio").analyze_portfolio(
        snapshot(positions=(stock(),), cash=None, completeness="partial"),
        policy(max_issuer_weight=D("0.5")),
        now=NOW,
    )
    assert result.status == "unavailable"
    assert result.availability["nav"] == "unknown"
    assert "nav" not in result.outputs
    assert result.parameters["policy_checks"]["max_issuer_weight"] == "unknown"


def test_assignment_limit_uses_gross_obligations_even_when_a_long_put_hedges_payoff():
    result = api("portfolio").analyze_portfolio(
        snapshot(positions=(put(), put("long", "1", "45"))),
        policy(max_gross_assignment_to_nav=D("0.9")),
        now=NOW,
    )
    assert_money(result, "gross_assignment_funding", "10000")
    assert result.parameters["policy_checks"]["max_gross_assignment_to_nav"] == "fail"


def test_missing_fx_keeps_currency_exposures_and_blocks_aggregate_even_with_reported_nav():
    result = api("portfolio").analyze_portfolio(
        snapshot(
            positions=(put(), put("eur-put", "-1", currency="EUR")),
            valuations=(amount("20000", unit="nav"),),
        ),
        policy(max_gross_assignment_to_nav=D("1")),
        now=NOW,
    )
    assert result.status == "unavailable"
    assert result.parameters["aggregate_complete"] is False
    assert result.parameters["gross_assignment_funding_by_currency"] == {
        "USD": "10000",
        "EUR": "5000",
    }
    assert result.availability["gross_assignment_funding"] == "unknown"
    assert "gross_assignment_funding" not in result.outputs
    assert result.parameters["policy_checks"]["max_gross_assignment_to_nav"] == "unknown"
    assert any("fx" in finding.code.lower() for finding in result.findings)


def test_unsupported_holding_is_visible_and_risk_aggregate_incomplete():
    result = api("portfolio").analyze_portfolio(
        snapshot(positions=(stock(instrument_type="structured_note"),)), policy(), now=NOW
    )
    assert result.status == "unavailable"
    assert result.parameters["aggregate_complete"] is False
    assert result.parameters["unsupported_positions"] == [
        {"position_id": "alpha-holding", "instrument_type": "structured_note"}
    ]
    assert any("alpha-holding" in finding.affected_ids for finding in result.findings)


def capacity(**changes):
    return BrokerCapacity(
        **(
            dict(
                availability="known",
                as_of=NOW,
                source="synthetic-broker",
                buying_power=amount("8000"),
                maintenance_margin=amount("2000"),
                excess_liquidity=amount("3000"),
            )
            | changes
        )
    )


def freshness_policy(**changes):
    return policy(
        **(
            dict(
                max_snapshot_age_seconds=3600,
                max_capacity_age_seconds=60,
                required_capacity_fields=("buying_power", "maintenance_margin", "excess_liquidity"),
            )
            | changes
        )
    )


def test_fresh_fetch_of_old_portfolio_preserves_analytics_but_not_current_capacity():
    result = api("portfolio").analyze_portfolio(
        snapshot(as_of=NOW - timedelta(days=1), fetched_at=NOW, broker_capacity=capacity()),
        freshness_policy(),
        now=NOW,
    )
    assert_money(result, "nav", "10000")
    assert_money(result, "broker_buying_power", "8000")
    assert result.parameters["policy_checks"]["max_snapshot_age_seconds"] == "fail"
    assert result.parameters["capacity_readiness"] == "unknown"


@pytest.mark.parametrize(
    "changes",
    [{"as_of": NOW - timedelta(minutes=2)}, {"maintenance_margin": None}, {"as_of": None}],
)
def test_missing_or_stale_required_capacity_never_passes_readiness(changes):
    result = api("portfolio").analyze_portfolio(
        snapshot(broker_capacity=capacity(**changes)), freshness_policy(), now=NOW
    )
    assert result.parameters["capacity_readiness"] == "unknown"
    assert_money(result, "broker_buying_power", "8000")


@pytest.mark.parametrize("required", [None, ()])
def test_absent_or_empty_capacity_requirements_do_not_fabricate_readiness(required):
    result = api("portfolio").analyze_portfolio(
        snapshot(broker_capacity=capacity()),
        freshness_policy(required_capacity_fields=required),
        now=NOW,
    )
    assert result.parameters["capacity_readiness"] == "unknown"


def test_fresh_explicit_required_broker_capacity_can_be_verified_separately():
    result = api("portfolio").analyze_portfolio(
        snapshot(broker_capacity=capacity()), freshness_policy(), now=NOW
    )
    assert result.parameters["capacity_readiness"] == "verified"
    assert_money(result, "broker_buying_power", "8000")
    assert_money(result, "gross_assignment_funding", "0")


@pytest.mark.parametrize("nav", ["0", "-100"])
def test_nonpositive_nav_keeps_policy_denominator_unknown_and_capacity_unverified(nav):
    result = api("portfolio").analyze_portfolio(
        snapshot(valuations=(amount(nav, unit="nav"),), broker_capacity=capacity()),
        freshness_policy(max_issuer_weight=D("0.5")),
        now=NOW,
    )
    assert_money(result, "nav", nav)
    assert result.parameters["aggregate_complete"] is False
    assert result.parameters["policy_checks"]["max_issuer_weight"] == "unknown"
    assert result.parameters["capacity_readiness"] == "unknown"


def test_unknown_cash_remains_incomplete_even_when_source_nav_is_known():
    result = api("portfolio").analyze_portfolio(
        snapshot(
            cash=(CashBalance(currency="USD", availability="unknown"),),
            valuations=(amount("10000", unit="nav"),),
            broker_capacity=capacity(),
        ),
        freshness_policy(),
        now=NOW,
    )
    assert_money(result, "nav", "10000")
    assert result.parameters["aggregate_complete"] is False
    assert result.parameters["capacity_readiness"] == "unknown"


def test_extreme_funding_ratio_returns_invalid_data_instead_of_raising_decimal_error():
    result = api("portfolio").analyze_portfolio(
        snapshot(positions=(put(),), valuations=(amount("1E-999999", unit="nav"),)),
        policy(max_gross_assignment_to_nav=D("1")),
        now=NOW,
    )
    assert result.status == "invalid_data"
    assert result.findings


def history_snapshot(identifier, observed, nav, flows=(), **changes):
    return snapshot(
        **(
            dict(
                snapshot_id=identifier,
                as_of=observed,
                fetched_at=observed,
                valuations=(amount(nav, unit="nav"),),
                flows=flows,
            )
            | changes
        )
    )


def deposit(identifier="deposit-1", value="2000", occurred_at=None, currency="USD", kind="deposit"):
    return CashFlow(
        flow_id=identifier,
        occurred_at=occurred_at or NOW + timedelta(hours=1),
        amount=D(value),
        currency=currency,
        kind=kind,
        source="synthetic-host",
    )


def test_nav_rise_from_deposit_is_zero_cash_flow_adjusted_investment_change():
    result = api("performance").performance(
        [
            history_snapshot("start", NOW, "10000"),
            history_snapshot("end", NOW + timedelta(days=1), "12000", (deposit(),)),
        ]
    )
    assert_money(result, "nav_change", "2000")
    assert_money(result, "net_external_flows", "2000")
    assert_money(result, "cash_flow_adjusted_change", "0")
    assert result.parameters["method"] == "nav_change_less_net_external_flows"
    assert result.availability["time_weighted_return"] == "unavailable"
    assert result.availability["tax_pnl"] == "unavailable"
    assert "time_weighted_return" not in result.outputs
    assert "tax_pnl" not in result.outputs
    assert set(result.input_refs) >= {"start", "end", "deposit-1"}
    assert result.formula and result.formula_version


def test_income_is_not_subtracted_as_an_external_deposit():
    result = api("performance").performance(
        [
            history_snapshot("start", NOW, "10000"),
            history_snapshot(
                "end", NOW + timedelta(days=1), "10100", (deposit(value="100", kind="income"),)
            ),
        ]
    )
    assert_money(result, "net_external_flows", "0")
    assert_money(result, "cash_flow_adjusted_change", "100")


def test_withdrawal_has_negative_account_sign_and_is_not_an_investment_loss():
    result = api("performance").performance(
        [
            history_snapshot("start", NOW, "10000"),
            history_snapshot(
                "end",
                NOW + timedelta(days=1),
                "9000",
                (deposit(value="-1000", kind="withdrawal"),),
            ),
        ]
    )
    assert_money(result, "nav_change", "-1000")
    assert_money(result, "net_external_flows", "-1000")
    assert_money(result, "cash_flow_adjusted_change", "0")


@pytest.mark.parametrize("kind, value", [("withdrawal", "1000"), ("deposit", "-1000")])
def test_inconsistent_external_flow_sign_is_rejected_without_absolute_value_guess(kind, value):
    result = api("performance").performance(
        [
            history_snapshot("start", NOW, "10000"),
            history_snapshot(
                "end", NOW + timedelta(days=1), "11000", (deposit(value=value, kind=kind),)
            ),
        ]
    )
    assert result.status == "invalid_data"
    assert "cash_flow_adjusted_change" not in result.outputs
    assert any(finding.code == "external_flow_sign_conflict" for finding in result.findings)


def test_invalid_external_flow_sign_is_not_hidden_by_missing_nav():
    result = api("performance").performance(
        [
            history_snapshot("start", NOW, "10000", valuations=None, cash=None),
            history_snapshot(
                "end", NOW + timedelta(days=1), "11000", (deposit(value="1000", kind="withdrawal"),)
            ),
        ]
    )
    assert result.status == "invalid_data"
    assert any(finding.code == "external_flow_sign_conflict" for finding in result.findings)


def test_zero_external_flow_remains_a_known_zero():
    result = api("performance").performance(
        [
            history_snapshot("start", NOW, "10000"),
            history_snapshot("end", NOW + timedelta(days=1), "10000", (deposit(value="0"),)),
        ]
    )
    assert_money(result, "net_external_flows", "0")
    assert_money(result, "cash_flow_adjusted_change", "0")


def test_unknown_flow_history_preserves_nav_change_without_inventing_profit():
    result = api("performance").performance(
        [
            history_snapshot("start", NOW, "10000"),
            history_snapshot("end", NOW + timedelta(days=1), "12000", flows=None),
        ]
    )
    assert result.status == "unavailable"
    assert_money(result, "nav_change", "2000")
    assert result.availability["cash_flow_adjusted_change"] == "unknown"
    assert "cash_flow_adjusted_change" not in result.outputs


def test_repeated_identical_flow_ids_are_counted_once_across_snapshots():
    flow = deposit()
    result = api("performance").performance(
        [
            history_snapshot("start", NOW, "10000"),
            history_snapshot("middle", NOW + timedelta(days=1), "12000", (flow,)),
            history_snapshot("end", NOW + timedelta(days=2), "12000", (flow,)),
        ]
    )
    assert_money(result, "net_external_flows", "2000")
    assert_money(result, "cash_flow_adjusted_change", "0")


def test_conflicting_duplicate_cash_flows_invalidate_performance():
    result = api("performance").performance(
        [
            history_snapshot("start", NOW, "10000"),
            history_snapshot("middle", NOW + timedelta(days=1), "12000", (deposit(),)),
            history_snapshot("end", NOW + timedelta(days=2), "12000", (deposit(value="3000"),)),
        ]
    )
    assert result.status == "invalid_data"
    assert "cash_flow_adjusted_change" not in result.outputs
    assert result.findings


def test_external_flows_use_open_start_closed_end_interval():
    end = NOW + timedelta(days=1)
    result = api("performance").performance(
        [
            history_snapshot("start", NOW, "10000"),
            history_snapshot(
                "end",
                end,
                "12000",
                (
                    deposit("at-start", "300", NOW),
                    deposit("at-end", "2000", end),
                    deposit("future", "500", end + timedelta(seconds=1)),
                ),
            ),
        ]
    )
    assert_money(result, "net_external_flows", "2000")
    assert_money(result, "cash_flow_adjusted_change", "0")


def test_foreign_cash_flow_without_fx_blocks_profit_aggregate():
    result = api("performance").performance(
        [
            history_snapshot("start", NOW, "10000"),
            history_snapshot("end", NOW + timedelta(days=1), "12000", (deposit(currency="EUR"),)),
        ]
    )
    assert result.status == "unavailable"
    assert "cash_flow_adjusted_change" not in result.outputs
    assert result.findings


def test_single_snapshot_retains_labelled_pnl_but_cannot_invent_returns_or_tax():
    result = api("performance").performance(
        [
            history_snapshot(
                "only",
                NOW,
                "10000",
                valuations=(
                    amount("10000", unit="nav"),
                    amount("100", unit="realized_pnl"),
                    amount("200", unit="unrealized_pnl"),
                ),
            )
        ]
    )
    assert result.status == "unavailable"
    assert_money(result, "realized_pnl", "100")
    assert_money(result, "unrealized_pnl", "200")
    for key in ("cash_flow_adjusted_change", "time_weighted_return", "tax_pnl"):
        assert key not in result.outputs
        assert result.availability[key] != "known"
    assert result.reason and result.findings
