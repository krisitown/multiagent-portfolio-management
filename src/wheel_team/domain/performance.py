"""Supported account-value changes; no inferred TWR, tax lots or FX rates."""

from decimal import Decimal, DecimalException, localcontext

from wheel_team.contracts import (
    Availability,
    CalculationRecord,
    CashFlow,
    JsonValue,
    PortfolioSnapshot,
)
from wheel_team.domain.coverage import ZERO, _record
from wheel_team.domain.money import arithmetic_context
from wheel_team.domain.portfolio import _finding, _labelled_value, _snapshot_nav


def performance(history: list[PortfolioSnapshot]) -> CalculationRecord:
    """Subtract supported net external cash flows from a comparable NAV change.

    Core normalization uses signed account cash: deposits >= 0, withdrawals
    <= 0. Hosts using positive withdrawal amounts must explicitly negate them
    in their adapter. Income and fees are internal and stay in NAV change.
    Supplied flows describe the available history; None is unknown, () is a
    supplied empty history. Applicable flow instants are (start, end].

    Unit labels nav/realized_pnl/unrealized_pnl identify supplied account totals
    in the declared currency. Source-labelled P&L is not tax-lot reconciliation.
    A cash-flow-adjusted absolute change is not a time-weighted return.
    """
    parameters: dict[str, JsonValue] = {
        "history": [snapshot.model_dump(mode="json") for snapshot in history],
        "method": "nav_change_less_net_external_flows",
        "external_flow_sign": "signed_account_deposits_nonnegative_withdrawals_nonpositive",
        "flow_interval": "open_start_closed_end",
        "pnl_method": "source_labelled_not_tax_reconciled",
        "time_weighted_return_method": "not_computed_insufficient_valuation_path",
        "tax_method": "not_computed_no_tax_lot_methodology",
    }
    unknown = {
        key: Availability.UNKNOWN
        for key in (
            "nav_change",
            "net_external_flows",
            "cash_flow_adjusted_change",
            "realized_pnl",
            "unrealized_pnl",
        )
    } | {"time_weighted_return": Availability.UNAVAILABLE, "tax_pnl": Availability.UNAVAILABLE}
    refs = tuple(dict.fromkeys(snapshot.snapshot_id for snapshot in history))
    findings = []
    outputs: dict[str, Decimal] = {}
    inputs: dict[str, Decimal] = {}
    currency = history[-1].base_currency if history else None
    invalid_reason: str | None = None
    seen_snapshots: dict[str, PortfolioSnapshot] = {}
    for snapshot in history:
        previous = seen_snapshots.get(snapshot.snapshot_id)
        if previous is not None and previous != snapshot:
            invalid_reason = "Conflicting content for the same immutable snapshot ID"
        seen_snapshots[snapshot.snapshot_id] = snapshot
    ordered = sorted(seen_snapshots.values(), key=lambda snapshot: snapshot.as_of)
    if len({snapshot.account_alias for snapshot in ordered}) > 1:
        invalid_reason = "Performance history mixes account aliases"
    if len({snapshot.as_of for snapshot in ordered}) != len(ordered):
        invalid_reason = "Distinct snapshots at one observation instant are ambiguous"
    if invalid_reason:
        return _record(
            "performance",
            parameters,
            outputs={},
            units={},
            refs=refs,
            availability=unknown,
            status="invalid_data",
            reason=invalid_reason,
            findings=(_finding("incomparable_history", invalid_reason, *refs),),
        )
    if ordered:
        currency = ordered[-1].base_currency
        latest = ordered[-1]
        for key in ("realized_pnl", "unrealized_pnl"):
            value, diagnostics = _labelled_value(latest, key)
            findings.extend(diagnostics)
            if value is not None:
                outputs[key] = value
    usable = len(ordered) >= 2
    if not usable:
        findings.append(
            _finding(
                "insufficient_history",
                "At least two distinct comparable NAV observations are required",
                *refs,
            )
        )
    elif len({snapshot.base_currency for snapshot in ordered}) > 1:
        usable = False
        findings.append(
            _finding(
                "performance_fx_missing",
                "Changing base currencies require explicit FX before performance comparison",
                *refs,
            )
        )
    try:
        with localcontext(arithmetic_context()):
            if usable:
                start, end = ordered[0], ordered[-1]
                parameters["interval_start"] = start.as_of.isoformat()
                parameters["interval_end"] = end.as_of.isoformat()
                first_nav, first_findings = _snapshot_nav(start)
                last_nav, last_findings = _snapshot_nav(end)
                findings.extend(first_findings + last_findings)
                if first_nav is not None and last_nav is not None:
                    inputs["start_nav"] = first_nav
                    inputs["end_nav"] = last_nav
                    outputs["nav_change"] = last_nav - first_nav
                else:
                    usable = False
            flows: dict[str, CashFlow] = {}
            for snapshot in ordered:
                for flow in snapshot.flows or ():
                    previous_flow = flows.get(flow.flow_id)
                    if previous_flow is not None and previous_flow != flow:
                        invalid_reason = "Conflicting records for the same cash-flow ID"
                        findings.append(
                            _finding("cash_flow_conflict", invalid_reason, flow.flow_id)
                        )
                    in_interval = (
                        len(ordered) >= 2
                        and ordered[0].as_of < flow.occurred_at <= ordered[-1].as_of
                    )
                    if in_interval and (
                        (flow.kind == "deposit" and flow.amount < 0)
                        or (flow.kind == "withdrawal" and flow.amount > 0)
                    ):
                        invalid_reason = (
                            "External flow sign conflicts with signed account convention"
                        )
                        findings.append(
                            _finding("external_flow_sign_conflict", invalid_reason, flow.flow_id)
                        )
                    flows[flow.flow_id] = flow
            refs = tuple(dict.fromkeys((*refs, *sorted(flows))))
            if usable:
                interval = [
                    flow
                    for flow in flows.values()
                    if ordered[0].as_of < flow.occurred_at <= ordered[-1].as_of
                ]
                parameters["applicable_flow_ids"] = [
                    flow.flow_id for flow in sorted(interval, key=lambda flow: flow.flow_id)
                ]
                flows_known = all(snapshot.flows is not None for snapshot in ordered)
                if not flows_known:
                    findings.append(
                        _finding(
                            "cash_flow_history_unknown",
                            "Missing cash-flow history cannot be assumed empty",
                            *refs,
                        )
                    )
                net_external = ZERO
                by_currency: dict[str, Decimal] = {}
                for flow in interval:
                    if flow.kind == "other":
                        flows_known = False
                        findings.append(
                            _finding(
                                "cash_flow_classification_unknown",
                                "Other flow lacks an external/internal classification",
                                flow.flow_id,
                            )
                        )
                    if flow.kind not in {"deposit", "withdrawal"}:
                        continue
                    by_currency[flow.currency] = by_currency.get(flow.currency, ZERO) + flow.amount
                    if flow.currency != currency:
                        flows_known = False
                        findings.append(
                            _finding(
                                "performance_fx_missing",
                                "Foreign external flow needs explicit FX",
                                flow.flow_id,
                            )
                        )
                    else:
                        net_external += flow.amount
                parameters["net_external_flows_by_currency"] = {
                    key: str(value) for key, value in sorted(by_currency.items())
                }
                if flows_known and invalid_reason is None:
                    outputs["net_external_flows"] = net_external
                    outputs["cash_flow_adjusted_change"] = outputs["nav_change"] - net_external
                else:
                    usable = False
    except DecimalException:
        invalid_reason = "Performance values exceed supported Decimal arithmetic"
        findings.append(_finding("invalid_performance_arithmetic", invalid_reason, *refs))
    if invalid_reason:
        return _record(
            "performance",
            parameters,
            outputs={},
            units={},
            refs=refs,
            availability=unknown,
            status="invalid_data",
            reason=invalid_reason,
            findings=tuple(findings),
        )
    units = {key: "currency" for key in outputs.keys() | inputs.keys()}
    currencies = {key: currency for key in units if currency is not None}
    unknown = {key: value for key, value in unknown.items() if key not in outputs}
    return _record(
        "performance",
        parameters,
        outputs=outputs,
        inputs=inputs,
        units=units,
        currencies=currencies,
        availability=unknown,
        refs=refs,
        findings=tuple(findings),
        status="success" if usable else "unavailable",
        reason="Supported history/cash flows are insufficient; see findings"
        if not usable
        else None,
    )
