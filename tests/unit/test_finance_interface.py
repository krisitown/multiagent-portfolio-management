"""Synthetic arithmetic fixtures; no private account values or holdings."""

from decimal import Decimal, localcontext

import pytest

from wheel_team.tools.finance import finance_tools
from wheel_team.vault import EvidenceStore


@pytest.fixture
def calculate(tmp_path):
    return finance_tools(EvidenceStore(tmp_path))["finance_calculate"]


def terms(quantity="-2", expiry="2027-01-15", right="call", strike="60", pid="short"):
    return {
        "position_id": pid,
        "quantity": quantity,
        "currency": "USD",
        "contract": {
            "instrument_id": pid,
            "instrument_type": "option",
            "currency": "USD",
            "underlying_id": "AAPL",
            "expiry": expiry,
            "option_right": right,
            "strike": strike,
            "multiplier": "100",
        },
    }


async def test_roll_cash_does_not_require_historical_opening_credit(calculate):
    result = await calculate(
        "roll_economics",
        {
            "quantity": "2",
            "multiplier": "100",
            "close_debit": "3.10",
            "new_credit": "4.20",
            "fees": "2",
        },
        {"currency": "USD"},
    )
    assert result["status"] == "success"
    data = result["data"]
    assert Decimal(data["outputs"]["net_roll_cash"]) == Decimal("218")
    assert "combined_history_cash" not in data["outputs"]
    assert data["availability"]["old_leg_realized_total"] == "unknown"
    assert data["availability"]["combined_history_cash"] == "unknown"


@pytest.mark.parametrize("formula", ["match_coverage", "analyze_portfolio"])
async def test_explicit_subset_positions_need_no_snapshot(calculate, formula):
    result = await calculate(
        formula,
        {},
        {
            "positions": [terms(), terms("2", strike="50", pid="long")],
            "base_currency": "USD",
            "as_of": "2026-10-30T20:00:00Z",
        },
    )
    assert result["status"] in {"success", "unavailable"}
    data = result["data"]
    coverage = data if formula == "match_coverage" else data["parameters"]["coverage"]
    assert coverage["outputs"]["unmatched_short_contracts"] == "0"
    if formula == "analyze_portfolio":
        assert data["parameters"]["aggregate_complete"] is False
        assert data["availability"]["nav"] == "unknown"


async def test_error_reports_missing_fields_without_echoing_private_payload(calculate):
    result = await calculate("roll_economics", {"quantity": "1", "private": "DO-NOT-ECHO"})
    assert result["status"] == "invalid_data"
    assert "close_debit" in result["reason"]
    assert "DO-NOT-ECHO" not in str(result)


async def test_generic_arithmetic_uses_independent_decimal_context(calculate):
    with localcontext() as context:
        context.prec = 3
        result = await calculate(
            "arithmetic",
            {"values": ["0.07", "80", "9"]},
            {
                "operation": "multiply",
                "result_unit": "currency_per_share",
            },
        )
    assert result["status"] == "success"
    assert Decimal(result["data"]["outputs"]["result"]) == Decimal("50.40")
    assert result["data"]["units"]["result"] == "currency_per_share"


async def test_fractional_contract_error_explains_units(calculate):
    result = await calculate(
        "put_economics",
        {"quantity": "1.5", "premium": "2", "fees": "0"},
        {"contract": terms()["contract"] | {"option_right": "put"}},
    )
    assert "contract count" in result["reason"]
    assert "share" in result["reason"]


@pytest.mark.parametrize(
    "operation,values,expected",
    [
        ("add", ["0.1", "0.2"], "0.3"),
        ("subtract", ["10", "3", "2"], "5"),
        ("divide", ["10", "4", "2"], "1.25"),
    ],
)
async def test_arithmetic_order_and_exact_decimal_values(calculate, operation, values, expected):
    result = await calculate("arithmetic", {"values": values}, {"operation": operation})
    assert Decimal(result["data"]["outputs"]["result"]) == Decimal(expected)


@pytest.mark.parametrize("values", [["1", "0"], ["1", 0.2], ["1", True], ["NaN", "2"]])
async def test_arithmetic_rejects_invalid_or_inexact_values(calculate, values):
    result = await calculate("arithmetic", {"values": values}, {"operation": "divide"})
    assert result["status"] == "invalid_data"


async def test_expiry_results_echo_share_scale_and_mixed_expiry_scope(calculate):
    result = await calculate(
        "expiry_payoff",
        {"AAPL": "70"},
        {
            "positions": [terms(), terms("2", "2027-02-19", strike="50", pid="long")],
        },
    )
    assert Decimal(result["data"]["outputs"]["expiry_payoff"]) == Decimal("2000")
    assert result["data"]["inputs"]["position_0_share_equivalent_quantity"] == "-200"
    assert result["data"]["parameters"]["mixed_expiry_interpretation"] == (
        "simultaneous_intrinsic_stress_not_one_dated_expiry_or_mark_to_market"
    )


async def test_coverage_does_not_consume_different_expiry_protection(calculate):
    result = await calculate(
        "match_coverage",
        {},
        {
            "positions": [terms(), terms("2", "2027-02-19", strike="50", pid="long")],
            "base_currency": "USD",
            "as_of": "2026-10-30T20:00:00Z",
        },
    )
    assert result["data"]["outputs"]["unmatched_short_contracts"] == "2"
