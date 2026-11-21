"""Synthetic arguments exercise the real SDK decorator without inference."""

import json
from decimal import Decimal

import pytest

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_agents import context
from wheel_team.agents.analyst import build_analyst
from wheel_team.tools.finance import finance_tools
from wheel_team.types import TeamResult
from wheel_team.vault import EvidenceStore


@pytest.mark.parametrize(
    "bad_inputs,bad_parameters,field",
    [
        (["406.97", "380"], {"operation": "divide"}, "inputs"),
        (["26.97", "406.97"], {"operation": "divide"}, "inputs"),
        ('{"values": ["PRIVATE-OPERAND"]', {"operation": "divide"}, "inputs"),
        (42, {"operation": "divide"}, "inputs"),
        (None, {"operation": "divide"}, "inputs"),
        ({"values": ["1", "2"]}, [], "parameters"),
        ({"values": ["1", "2"]}, "", "parameters"),
        ({"values": ["1", "2"]}, False, "parameters"),
        ({"values": ["1", "2"]}, 0, "parameters"),
    ],
)
async def test_sdk_shape_error_is_safe_and_correctable(tmp_path, bad_inputs, bad_parameters, field):
    model = ScriptModel(
        output(
            "finance_calculate", formula="arithmetic", inputs=bad_inputs, parameters=bad_parameters
        ),
        output(
            "finance_calculate",
            formula="arithmetic",
            inputs={"values": ["12", "3"]},
            parameters={"operation": "divide"},
        ),
        output("TeamResult", summary="Synthetic recovery"),
    )
    agent = build_analyst(
        context(tmp_path, "analyst", model, finance_tools(EvidenceStore(tmp_path / "records")))
    )
    await agent.invoke_async("Synthetic recovery", structured_output_model=TeamResult)
    first = model.calls[1]["messages"][-1]["content"][0]["toolResult"]
    assert first["status"] == "success"
    rejected = json.loads(first["content"][0]["text"])
    assert rejected["status"] == "invalid_data"
    assert field in rejected["reason"]
    assert "PRIVATE-OPERAND" not in str(rejected)
    assert "406.97" not in str(rejected)
    second = model.calls[2]["messages"][-1]["content"][0]["toolResult"]
    accepted = json.loads(second["content"][0]["text"])
    assert accepted["status"] == "success"
    assert accepted["data"]["outputs"]["result"] == "4"
    spec = next(s for s in model.calls[0]["tools"] if s["name"] == "finance_calculate")
    schema = spec["inputSchema"]["json"]
    # Exact SDK-advertised shape from checkpoint444cad1 (SDK simplifies schemas).
    assert schema == {
        "type": "object",
        "required": ["formula", "inputs"],
        "properties": {
            "formula": {
                "description": "Parameter formula",
                "type": "string",
                "enum": [
                    "put_economics",
                    "roll_economics",
                    "expiry_payoff",
                    "match_coverage",
                    "analyze_portfolio",
                    "performance",
                    "arithmetic",
                ],
            },
            "inputs": {"description": "Parameter inputs", "type": "object"},
            "parameters": {
                "default": None,
                "description": "Parameter parameters",
                "type": "object",
            },
        },
    }


async def test_arithmetic_echoes_evaluation_and_scope_without_interpreting_label(tmp_path):
    calculate = finance_tools(EvidenceStore(tmp_path))["finance_calculate"]
    result = await calculate(
        "arithmetic",
        {"values": ["0.65", "1.825", "2.70", "4.00"]},
        {"operation": "subtract", "result_unit": "(C-P)-(S-K)"},
    )
    data = result["data"]
    assert data["outputs"]["result"] == "-7.875"
    assert data["parameters"]["evaluated_expression"] == "(((0.65 - 1.825) - 2.70) - 4.00)"
    assert data["parameters"]["verification_scope"] == "supplied_arithmetic_only"
    assert data["parameters"]["source_verified"] is False


async def test_parentheses_require_chaining_and_null_parameters_remain_optional(tmp_path):
    calculate = finance_tools(EvidenceStore(tmp_path))["finance_calculate"]

    async def subtract(values):
        r = await calculate("arithmetic", {"values": values}, {"operation": "subtract"})
        return r["data"]["outputs"]["result"]

    left = await subtract(["0.65", "1.825"])
    right = await subtract(["2.70", "4.00"])
    assert await subtract([left, right]) == "0.125"
    inputs = {
        "quantity": "2",
        "multiplier": "100",
        "close_debit": "3.10",
        "new_credit": "4.20",
        "fees": "2",
    }
    for kwargs in ({}, {"parameters": None}):
        r = await calculate("roll_economics", inputs, **kwargs)
        assert r["status"] == "success"
        assert Decimal(r["data"]["outputs"]["net_roll_cash"]) == Decimal("218")
        assert "combined_history_cash" not in r["data"]["outputs"]
