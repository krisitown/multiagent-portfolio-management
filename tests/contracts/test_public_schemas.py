"""Boundary regressions: unavailable evidence must never imply a known zero."""

import importlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError


def contracts():
    return importlib.import_module("wheel_team.contracts")


def snapshot_payload():
    return {
        "snapshot_id": "snapshot-synthetic-1",
        "account_alias": "invented-account",
        "source": "synthetic",
        "as_of": "2026-10-30T20:00:00Z",
        "fetched_at": "2026-10-30T20:05:00Z",
        "base_currency": "USD",
        "completeness": "partial",
    }


def option_payload():
    return {
        "instrument_id": "option-adjusted-1",
        "instrument_type": "option",
        "symbol": "ALPHA",
        "currency": "USD",
        "underlying_id": "issuer-alpha",
        "expiry": "2026-12-18",
        "option_right": "put",
        "strike": "50.125",
        "multiplier": "150",
        "deliverable": {"shares": "125", "cash": "12.50"},
        "adjustment": "synthetic split",
        "venue": "SYNTHETIC",
    }


def test_missing_capacity_and_positions_are_unknown_not_empty():
    snapshot = contracts().PortfolioSnapshot.model_validate(snapshot_payload())
    assert snapshot.broker_capacity is None
    assert snapshot.positions is None
    assert snapshot.cash is None
    assert snapshot.model_fields_set.isdisjoint({"broker_capacity", "positions", "cash"})
    empty = contracts().PortfolioSnapshot.model_validate({**snapshot_payload(), "positions": []})
    assert empty.positions == ()


def test_explicit_zero_cash_and_decimal_json_roundtrip():
    snapshot = contracts().PortfolioSnapshot.model_validate(
        {
            **snapshot_payload(),
            "cash": [{"currency": "USD", "amount": "0", "availability": "known"}],
        }
    )
    assert snapshot.cash[0].amount == Decimal("0")
    assert json.loads(snapshot.model_dump_json())["cash"][0]["amount"] == "0"
    assert contracts().PortfolioSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


@pytest.mark.parametrize("field", ["as_of", "fetched_at"])
def test_portfolio_rejects_naive_required_instants(field):
    with pytest.raises(ValidationError, match=field):
        contracts().PortfolioSnapshot.model_validate(
            {**snapshot_payload(), field: "2026-10-30T20:00:00"}
        )


@pytest.mark.parametrize(
    "extra", [{"schema_version": 2}, {"idempotency_key": "legacy"}, {"group_id": "legacy"}]
)
def test_thin_request_rejects_unsupported_legacy_fields(extra):
    from wheel_team.types import TeamRequest

    with pytest.raises(ValidationError):
        TeamRequest.model_validate({"question": "Research ALPHA", "mode": "focused", **extra})


def test_option_identity_keeps_adjustment_and_required_terms():
    identity = contracts().ContractIdentity.model_validate(option_payload())
    assert identity.multiplier == Decimal("150")
    assert identity.deliverable == {"shares": "125", "cash": "12.50"}
    assert identity.strike == Decimal("50.125")
    assert contracts().ContractIdentity.model_validate_json(identity.model_dump_json()) == identity
    for missing in (
        "instrument_id",
        "currency",
        "underlying_id",
        "expiry",
        "strike",
        "multiplier",
        "option_right",
    ):
        payload = option_payload()
        del payload[missing]
        with pytest.raises(ValidationError, match=missing):
            contracts().ContractIdentity.model_validate(payload)


def test_unsupported_instruments_are_retained_and_input_json_is_copied():
    details = {"issuer_description": {"terms": ["unsupported synthetic future"]}}
    snapshot = contracts().PortfolioSnapshot.model_validate(
        {
            **snapshot_payload(),
            "positions": [
                {
                    "position_id": "position-1",
                    "quantity": "-2",
                    "currency": "USD",
                    "contract": {
                        "instrument_id": "future-1",
                        "instrument_type": "future",
                        "currency": "USD",
                        "raw": details,
                    },
                }
            ],
        }
    )
    details["issuer_description"]["terms"].append("mutated")
    position = snapshot.positions[0]
    assert position.contract.instrument_type == "future"
    assert position.contract.raw["issuer_description"]["terms"] == ["unsupported synthetic future"]
    assert position.quantity == Decimal("-2")
    with pytest.raises(ValidationError, match="frozen"):
        position.quantity = Decimal("0")


def test_missing_and_unknown_amount_remain_distinct_from_zero():
    cash = contracts().CashBalance(currency="USD", availability="unknown")
    assert cash.amount is None
    with pytest.raises(ValidationError):
        contracts().CashBalance(currency="USD", availability="known")
    with pytest.raises(ValidationError):
        contracts().CashBalance(currency="USD", availability="unavailable", amount="0")


def test_unresolved_source_time_keeps_raw_value_without_invented_timezone():
    record = contracts().EvidenceRecord(
        evidence_id="evidence-1",
        source="synthetic",
        fetched_at="2026-10-30T20:00:00Z",
        source_time_raw="2026-10-30 16:00",
        source_time_availability="unknown",
        timezone_state="unknown",
        delay_state="unknown",
        content={"price": "100"},
    )
    assert record.source_timestamp is None
    assert record.source_time_raw == "2026-10-30 16:00"
    with pytest.raises(ValidationError):
        contracts().EvidenceRecord.model_validate(
            {**record.model_dump(), "source_timestamp": "2026-10-30T16:00:00"}
        )


def test_unavailable_slots_cannot_contain_fake_candidates_and_statuses_are_explicit():
    c = contracts()
    slot = {"slot": 1, "kind": "unavailable", "reason": "No trustworthy evidence"}
    assert c.CandidateSlot.model_validate(slot).idea is None
    with pytest.raises(ValidationError):
        c.CandidateSlot.model_validate({**slot, "kind": "candidate"})
    assert {s.value for s in c.CandidateStatus} == {
        "Ready for user review",
        "Do not execute now — condition",
        "Do not execute now — unverified",
    }


def test_thin_limits_are_explicit_and_legacy_model_cap_is_rejected():
    from wheel_team.config import ModelConfig, TeamConfig

    config = TeamConfig(model=ModelConfig(model_id="offline"))
    assert config.max_generations == 100
    assert config.deadline_seconds == 2700
    assert config.max_expert_consultations == 5
    assert config.analyst_concurrency == 4
    with pytest.raises(ValidationError):
        TeamConfig(model=config.model, max_expert_consultations=6)
    with pytest.raises(ValidationError):
        ModelConfig(model_id="offline", max_generations=8)


def test_tool_boundary_rejects_fabricated_success_shapes():
    with pytest.raises(ValidationError):
        contracts().ToolResult(status="success")
    assert contracts().ToolResult(status="unavailable", reason="No provider").data is None


def test_exported_schemas_cover_all_required_interfaces(tmp_path):
    c = contracts()
    c.export_schemas(tmp_path)
    expected = [model.__name__ for model in c.PUBLIC_MODELS] + [
        "ModelConfig",
        "PolicyConfig",
        "PromptSet",
        "TeamConfig",
        "ExpertResult",
        "ResearchJob",
        "ResearchResult",
        "TeamRequest",
        "TeamResult",
    ]
    assert {path.stem for path in tmp_path.glob("*.json")} == set(expected)
    checked_in = Path(__file__).parents[2] / "schemas"
    assert {path.stem for path in checked_in.glob("*.json")} == set(expected)
    for name in expected:
        schema = json.loads((tmp_path / f"{name}.json").read_text())
        assert schema["title"] == name
        if "schema_version" in schema["properties"]:
            assert schema["properties"]["schema_version"]["const"] == 1
        assert schema == json.loads((checked_in / f"{name}.json").read_text())
    cash_schema = json.loads((tmp_path / "CashBalance.json").read_text())
    assert {part["type"] for part in cash_schema["properties"]["amount"]["anyOf"]} == {
        "string",
        "null",
    }


def test_financial_values_reject_nonfinite_and_preserve_precision():
    c = contracts()
    record = c.CalculationRecord(
        calculation_id="calc-1",
        status="success",
        formula="synthetic",
        formula_version="1",
        inputs={"premium": "123456789.123456789"},
        outputs={"credit": "123456789.123456789", "gross_assignment_funding": "10000"},
        units={
            "premium": "per_share",
            "credit": "currency",
            "gross_assignment_funding": "currency",
        },
        currencies={"premium": "USD", "credit": "USD", "gross_assignment_funding": "USD"},
        availability={
            "credit": "known",
            "gross_assignment_funding": "known",
            "buying_power": "unknown",
        },
        input_refs=["evidence-1"],
    )
    assert record.outputs["gross_assignment_funding"] == Decimal("10000")
    assert json.loads(record.model_dump_json())["outputs"]["credit"] == "123456789.123456789"
    restored = c.CalculationRecord.model_validate_json(record.model_dump_json())
    assert restored.outputs["credit"] == Decimal("123456789.123456789")
    assert restored.inputs["premium"] == Decimal("123456789.123456789")
    assert "buying_power" not in restored.outputs
    assert restored.availability["buying_power"] == "unknown"
    for value in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValidationError):
            c.CalculationRecord(
                calculation_id="bad",
                status="invalid_data",
                formula="test",
                formula_version="1",
                inputs={"premium": value},
            )


def test_supported_roles_reject_benchmark_prompt_binding():
    c = contracts()
    assert {role.value for role in c.AgentRole} == {
        "team_lead",
        "analyst",
        "wheel_expert",
        "explorer",
    }
    with pytest.raises(ValidationError):
        c.PromptBundle(
            prompt_id="benchmark",
            role="benchmark_expert",
            version="1",
            content="development fixture",
            content_hash="synthetic",
            status="development",
        )


def test_public_synthetic_examples_validate():
    examples = Path(__file__).parents[2] / "schemas" / "examples"
    c = contracts()
    snapshot = c.PortfolioSnapshot.model_validate_json(
        (examples / "portfolio-partial-v1.json").read_text()
    )
    assert snapshot.positions is None
    assert snapshot.broker_capacity is None
    from wheel_team.types import TeamRequest

    request = TeamRequest.model_validate_json((examples / "team-request-v1.json").read_text())
    assert request.portfolio.snapshot_id == snapshot.snapshot_id


def test_unknown_calculation_quantity_cannot_hide_an_explicit_zero():
    with pytest.raises(ValidationError, match="availability"):
        contracts().CalculationRecord(
            calculation_id="calc-unknown",
            status="success",
            formula="synthetic",
            formula_version="1",
            outputs={"buying_power": "0"},
            availability={"buying_power": "unknown"},
        )
