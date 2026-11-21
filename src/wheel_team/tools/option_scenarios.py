"""Run-local persisted hypothetical option calculations."""

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from wheel_team.contracts import ToolResult
from wheel_team.domain.money import calculation_record
from wheel_team.domain.option_pricing import ScenarioRequest, scenario_values
from wheel_team.vault import EvidenceStore


def option_scenario_tools(
    store: EvidenceStore,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    async def option_scenarios(request: dict[str, Any] | str) -> dict[str, Any]:
        """Model one hypothetical underlying/currency position; never a quote or probability.

        Decimal values are strings, quantity signed whole CONTRACTS (+long/-short),
        multiplier shares/contract, stock_shares signed SHARES, premium currency/share.
        entry_fees/exit_fees are total currency amounts charged once. Supply standard
        deliverables and explicit exercise_style. All leg expiries must be on/after
        valuation_date. Scenario as_of cannot pass any leg expiry: mixed-expiry
        settlement paths are unsupported. Expiry uses exact intrinsic; before expiry
        supply annual volatility (e.g. "0.30"), continuously compounded rate and
        continuous dividend_yield. leg_volatilities maps leg_id to IV, with common
        volatility as fallback. American CRR is finite-tree approximate, European BSM
        assumes constant inputs. No discrete dividends, adjusted contracts, FX, borrow,
        financing/margin, exercise costs or executable price inference. Stock dividends
        are excluded. tree_steps 25..1000 (default300), finite workload cap applies.
        Terminal risk bounds apply to common-expiry payoff only and do not establish
        broker capital required or losses from early assignment. Avoid labeling initial
        debit as maximum loss when short exposure remains. Source units/deliverables
        must be independently verified; this tool verifies supplied math only.
        """
        try:
            if isinstance(request, str):
                request = json.loads(request)
            terms = ScenarioRequest.model_validate(request)
            result = await asyncio.to_thread(scenario_values, terms)
            record = (
                calculation_record(
                    "option_scenarios",
                    {},
                    {"request": terms.model_dump(mode="json"), "result": result},
                )
                .model_copy(update={"computed_at": clock()})
                .model_dump(mode="json")
            )
            record.pop("calculation_id")
            digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
            record["calculation_id"] = "calculation:" + digest
            ref = store.put("calculation", record)
            return ToolResult(status="success", data=result, result_refs=(ref,)).model_dump(
                mode="json"
            )
        except (ValueError, TypeError, ArithmeticError, OverflowError) as exc:
            if isinstance(exc, ValidationError):
                reason = "; ".join(
                    ".".join(map(str, error["loc"])) + ": " + error["type"]
                    for error in exc.errors(include_input=False, include_context=False)
                )
            else:
                reason = str(exc)
            return ToolResult(status="invalid_data", reason=reason).model_dump(mode="json")

    return {"option_scenarios": option_scenarios}
