"""Reuse the existing calculator with signed quantities and persisted originals."""

from wheel_team.tools.option_scenarios import option_scenario_tools
from wheel_team.vault import EvidenceStore


async def test_short_put_expiry_math_and_original_calculation_reference(tmp_path):
    store = EvidenceStore(tmp_path / "records")
    tool = option_scenario_tools(store)["option_scenarios"]
    request = {
        "currency": "USD",
        "valuation_date": "2026-11-10",
        "legs": [
            {
                "leg_id": "put",
                "option_right": "put",
                "strike": "50",
                "expiry": "2026-12-18",
                "quantity": -2,
                "multiplier": "100",
                "premium": "3",
                "exercise_style": "american",
                "deliverable": "standard",
            }
        ],
        "scenarios": [
            {"label": "down", "spot": "40", "as_of": "2026-12-18"},
            {"label": "up", "spot": "55", "as_of": "2026-12-18"},
        ],
    }
    reply = await tool(request)
    assert reply["status"] == "success"
    assert reply["data"]["entry_cash"] == "600"
    assert [row["pnl"] for row in reply["data"]["scenarios"]] == ["-1400", "600"]
    assert reply["data"]["terminal_risk"]["max_loss"] == "9400"
    assert store.get(reply["result_refs"][0])["formula"] == "option_scenarios"
    bad = await tool({**request, "entry_fees": 0.5})
    assert bad["status"] == "invalid_data" and not bad["result_refs"]
