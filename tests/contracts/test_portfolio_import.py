"""Synthetic snapshot ingestion preserves identities, unknowns and source dates."""

import importlib
import json
from copy import deepcopy
from decimal import Decimal

import pytest
from pydantic import ValidationError

from portfolio_management.contracts import PortfolioSnapshot


def importer():
    # Keep collection healthy before implementation; do not mask nested import bugs.
    try:
        return importlib.import_module("portfolio_management.adapters.portfolio.snapshot").import_snapshot
    except ModuleNotFoundError as exc:
        if exc.name not in {
            "portfolio_management.adapters.portfolio",
            "portfolio_management.adapters.portfolio.snapshot",
        }:
            raise
        pytest.fail("missing portfolio snapshot importer")


def payload(**changes):
    return {
        "schema_version": 1,
        "snapshot_id": "snapshot-invented-1",
        "account_alias": "synthetic-account",
        "source": "synthetic-host",
        "as_of": "2026-10-29T20:00:00Z",
        "fetched_at": "2026-10-30T20:05:00Z",
        "base_currency": "USD",
        "completeness": "partial",
    } | changes


def option_position(**changes):
    return {
        "position_id": "adjusted-put",
        "quantity": "-2",
        "currency": "EUR",
        "contract": {
            "instrument_id": "ALPHA-adjusted-put",
            "instrument_type": "option",
            "symbol": "ALPHA",
            "underlying_id": "issuer-alpha",
            "expiry": "2026-12-18",
            "option_right": "put",
            "strike": "50.125",
            "multiplier": "150",
            "currency": "EUR",
            "deliverable": {"shares": "125", "cash": "12.50"},
            "adjustment": "invented corporate action",
            "venue": "SYNTHETIC",
            "provider_ids": {"host": "invented-identifier"},
        },
    } | changes


def test_import_missing_collections_remain_unknown_with_diagnostics():
    snapshot = importer()(payload())
    assert isinstance(snapshot, PortfolioSnapshot)
    assert snapshot.positions is None
    assert snapshot.cash is None
    assert snapshot.broker_capacity is None
    assert snapshot.completeness == "partial"
    assert snapshot.findings
    assert all(finding.reason for finding in snapshot.findings)


def test_import_explicit_empty_account_differs_from_missing_account():
    snapshot = importer()(payload(positions=[], cash=[], completeness="complete"))
    assert snapshot.positions == ()
    assert snapshot.cash == ()


def test_import_preserves_decimal_identity_deliverable_and_source_as_of():
    snapshot = importer()(payload(positions=[option_position()]))
    position = snapshot.positions[0]
    assert position.quantity == Decimal("-2")
    assert position.contract.strike == Decimal("50.125")
    assert position.contract.multiplier == Decimal("150")
    assert position.contract.deliverable == {"shares": "125", "cash": "12.50"}
    assert position.contract.provider_ids == {"host": "invented-identifier"}
    assert snapshot.as_of.isoformat() == "2026-10-29T20:00:00+00:00"
    assert snapshot.fetched_at.isoformat() == "2026-10-30T20:05:00+00:00"
    encoded = json.loads(snapshot.model_dump_json())
    assert encoded["positions"][0]["contract"]["strike"] == "50.125"


def test_import_unsupported_holding_remains_visible_with_source_value():
    snapshot = importer()(
        payload(
            positions=[
                {
                    "position_id": "unsupported-note",
                    "quantity": "3",
                    "currency": "USD",
                    "contract": {
                        "instrument_id": "ALPHA-structured-note",
                        "instrument_type": "structured_note",
                        "currency": "USD",
                        "raw": {"description": "synthetic note", "terms": ["opaque payoff"]},
                    },
                    "valuation": {
                        "availability": "known",
                        "value": "321.50",
                        "unit": "currency",
                        "currency": "USD",
                        "source": "synthetic-host",
                    },
                }
            ]
        )
    )
    holding = snapshot.positions[0]
    assert holding.contract.instrument_type == "structured_note"
    assert holding.valuation.value == Decimal("321.50")
    assert holding.contract.raw["terms"] == ["opaque payoff"]
    assert any("unsupported-note" in finding.affected_ids for finding in snapshot.findings)


def test_import_known_zero_cash_differs_from_unknown_cash_and_capacity():
    snapshot = importer()(
        payload(
            cash=[
                {"currency": "USD", "availability": "known", "amount": "0"},
                {"currency": "EUR", "availability": "unknown", "reason": "not supplied"},
            ],
            broker_capacity={"availability": "unknown", "reason": "host cannot provide"},
        )
    )
    assert snapshot.cash[0].amount == Decimal("0")
    assert snapshot.cash[1].amount is None
    assert snapshot.broker_capacity.availability == "unknown"
    assert snapshot.broker_capacity.buying_power is None


def test_import_detaches_caller_json_without_mutating_input():
    supplied = payload(positions=[option_position()])
    original = deepcopy(supplied)
    snapshot = importer()(supplied)
    assert supplied == original
    supplied["positions"][0]["contract"]["deliverable"]["shares"] = "999"
    assert snapshot.positions[0].contract.deliverable["shares"] == "125"
    with pytest.raises(ValidationError, match="frozen"):
        snapshot.account_alias = "changed"


@pytest.mark.parametrize("version", [2, "1", True, 1.0])
def test_import_rejects_unsupported_or_noninteger_schema_version(version):
    load = importer()
    with pytest.raises(ValidationError):
        load(payload(schema_version=version))


@pytest.mark.parametrize("field", ["as_of", "fetched_at"])
def test_import_rejects_naive_required_instants(field):
    load = importer()
    with pytest.raises(ValidationError):
        load(payload(**{field: "2026-10-30T20:00:00"}))


@pytest.mark.parametrize(
    "missing", ["underlying_id", "expiry", "option_right", "strike", "multiplier"]
)
def test_import_rejects_option_missing_required_terms(missing):
    load = importer()
    position = option_position()
    del position["contract"][missing]
    with pytest.raises(ValidationError):
        load(payload(positions=[position]))


def test_import_rejects_identity_currency_conflict():
    load = importer()
    with pytest.raises(ValidationError):
        load(payload(positions=[option_position(currency="USD")]))


def test_import_rejects_duplicate_position_ids_instead_of_counting_twice():
    load = importer()
    with pytest.raises(ValueError):
        load(payload(positions=[option_position(), option_position()]))
