"""Coverage allocations consume each protective option or eligible share once."""

import importlib
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from wheel_team.contracts import CalculationRecord, ContractIdentity, PortfolioSnapshot, Position

D = Decimal
OBSERVED = datetime(2026, 10, 30, 20, tzinfo=UTC)


def coverage(snapshot):
    try:
        module = importlib.import_module("wheel_team.domain.coverage")
    except ModuleNotFoundError as exc:
        if exc.name not in {"wheel_team.domain", "wheel_team.domain.coverage"}:
            raise
        pytest.fail("missing portfolio coverage implementation")
    return module.match_coverage(snapshot)


def option(identifier="ALPHA-put-50", **changes):
    return ContractIdentity(
        **(
            {
                "instrument_id": identifier,
                "instrument_type": "option",
                "underlying_id": "issuer-alpha",
                "symbol": "ALPHA",
                "expiry": date(2026, 12, 18),
                "option_right": "put",
                "strike": D("50"),
                "multiplier": D("100"),
                "currency": "USD",
            }
            | changes
        )
    )


def position(identifier, quantity, identity=None, **changes):
    identity = identity or option()
    return Position(
        position_id=identifier,
        contract=identity,
        quantity=D(quantity),
        currency=identity.currency,
        **changes,
    )


def snapshot(*positions):
    return PortfolioSnapshot(
        snapshot_id="snapshot-coverage",
        account_alias="synthetic-account",
        source="synthetic-host",
        as_of=OBSERVED,
        fetched_at=OBSERVED,
        base_currency="USD",
        completeness="complete",
        positions=positions,
        cash=(),
    )


def assert_count(record, name, expected):
    assert isinstance(record, CalculationRecord)
    assert record.outputs[name] == D(expected)
    assert record.availability[name] == "known"
    assert record.units[name] == "contracts"


def test_two_shorts_one_long_leaves_one_unmatched_and_preserves_strike_gap():
    record = coverage(
        snapshot(
            position("shorts", "-2"),
            position("protective-long", "1", option("ALPHA-put-45", strike=D("45"))),
        )
    )
    assert_count(record, "matched_option_contracts", "1")
    assert_count(record, "unmatched_short_contracts", "1")
    # One 50/45 put spread still has a 5 * 100 contractual strike gap.
    assert record.outputs["matched_strike_gap_exposure"] == D("500")
    assert record.currencies["matched_strike_gap_exposure"] == "USD"
    assert record.parameters["coverage_allocations"] == [
        {
            "short_position_id": "shorts",
            "protection_position_id": "protective-long",
            "contracts": "1",
        }
    ]
    assert record.availability["broker_margin"] == "unknown"
    assert "broker_margin" not in record.outputs
    assert set(record.input_refs) == {"snapshot-coverage", "shorts", "protective-long"}
    assert record.formula and record.formula_version


def test_one_long_is_not_reused_across_two_short_rows():
    record = coverage(
        snapshot(position("short-a", "-1"), position("short-b", "-1"), position("long", "1"))
    )
    assert_count(record, "matched_option_contracts", "1")
    assert_count(record, "unmatched_short_contracts", "1")
    allocations = record.parameters["coverage_allocations"]
    assert sum(D(row["contracts"]) for row in allocations) == D("1")


@pytest.mark.parametrize(
    "changes",
    [
        {"underlying_id": "issuer-beta"},
        {"expiry": date(2027, 1, 15)},
        {"option_right": "call"},
        {"currency": "EUR"},
        {"multiplier": D("10")},
    ],
)
def test_same_ticker_does_not_match_incompatible_contract_family(changes):
    record = coverage(
        snapshot(position("short", "-1"), position("long", "1", option("different", **changes)))
    )
    assert_count(record, "matched_option_contracts", "0")
    assert_count(record, "unmatched_short_contracts", "1")


def test_different_deliverable_does_not_receive_plain_vanilla_protection():
    record = coverage(
        snapshot(
            position("short", "-1"),
            position("adjusted-long", "1", option("adjusted", deliverable={"cash": "5000"})),
        )
    )
    assert record.status == "unavailable"
    assert any("adjusted-long" in finding.affected_ids for finding in record.findings)
    assert not record.parameters["coverage_allocations"]


def shares(quantity, encumbered):
    return position(
        "shares",
        quantity,
        ContractIdentity(
            instrument_id="issuer-alpha", instrument_type="stock", currency="USD", symbol="ALPHA"
        ),
        encumbered_quantity=D(encumbered) if encumbered is not None else None,
    )


def test_previously_encumbered_shares_cannot_cover_another_call():
    record = coverage(
        snapshot(
            shares("200", "100"),
            position("calls", "-2", option("ALPHA-call", option_right="call")),
        )
    )
    assert_count(record, "share_covered_call_contracts", "1")
    assert_count(record, "unmatched_short_contracts", "1")


def test_unencumbered_shares_are_not_reused_across_call_positions():
    call = option("ALPHA-call", option_right="call")
    record = coverage(
        snapshot(shares("100", "0"), position("a", "-1", call), position("b", "-1", call))
    )
    assert_count(record, "share_covered_call_contracts", "1")
    assert_count(record, "unmatched_short_contracts", "1")


def test_unknown_encumbrance_does_not_become_free_share_capacity():
    record = coverage(
        snapshot(shares("100", None), position("call", "-1", option(option_right="call")))
    )
    assert record.status == "unavailable"
    assert not record.parameters["coverage_allocations"]
    assert any("shares" in finding.affected_ids for finding in record.findings)


def test_nonstandard_multiplier_consumes_actual_eligible_shares():
    record = coverage(
        snapshot(
            shares("25", "0"),
            position("calls", "-3", option(option_right="call", multiplier=D("10"))),
        )
    )
    assert_count(record, "share_covered_call_contracts", "2")
    assert_count(record, "unmatched_short_contracts", "1")


@pytest.mark.parametrize("quantity", ["-0.5", "0.5", "0"])
def test_zero_or_fractional_option_quantities_do_not_silently_match(quantity):
    record = coverage(snapshot(position("invalid", quantity), position("long", "1")))
    assert record.status == "invalid_data"
    assert not record.outputs
    assert record.findings


def test_two_short_positions_never_protect_each_other():
    record = coverage(snapshot(position("short-a", "-1"), position("short-b", "-1")))
    assert_count(record, "matched_option_contracts", "0")
    assert_count(record, "unmatched_short_contracts", "2")


def test_long_only_positions_do_not_create_short_obligations():
    record = coverage(snapshot(position("long-a", "1"), position("long-b", "2")))
    assert_count(record, "matched_option_contracts", "0")
    assert_count(record, "unmatched_short_contracts", "0")


def test_absent_positions_cannot_be_treated_as_known_empty_coverage():
    absent = snapshot().model_copy(update={"positions": None, "completeness": "partial"})
    record = coverage(absent)
    assert record.status == "unavailable"
    assert record.availability["unmatched_short_contracts"] == "unknown"
    assert "unmatched_short_contracts" not in record.outputs
