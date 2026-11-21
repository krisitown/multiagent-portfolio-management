"""Source-denominated portfolio values, gross obligations and explicit policy."""

from datetime import UTC, datetime
from decimal import Decimal, DecimalException, localcontext

from wheel_team.config import PolicyConfig
from wheel_team.contracts import (
    Availability,
    CalculationRecord,
    Finding,
    JsonValue,
    PortfolioSnapshot,
)
from wheel_team.domain.coverage import ZERO, _record, _unsupported, match_coverage
from wheel_team.domain.money import arithmetic_context


def _finding(code: str, reason: str, *refs: str) -> Finding:
    return Finding(code=code, severity="warning", affected_ids=refs, reason=reason)


def _labelled_value(
    snapshot: PortfolioSnapshot, label: str
) -> tuple[Decimal | None, list[Finding]]:
    rows = [row for row in snapshot.valuations or () if row.unit == label]
    if not rows:
        return None, []
    if len(rows) != 1:
        return None, [
            _finding(
                "ambiguous_valuation",
                f"Multiple {label} records have no reconciliation rule",
                snapshot.snapshot_id,
            )
        ]
    row = rows[0]
    if row.availability != Availability.KNOWN:
        return None, [
            _finding("valuation_unknown", f"Source {label} value is unknown", snapshot.snapshot_id)
        ]
    if row.currency != snapshot.base_currency:
        return None, [
            _finding(
                "valuation_fx_missing",
                f"Source {label} needs explicit denomination/FX",
                snapshot.snapshot_id,
            )
        ]
    return row.value, []


def _snapshot_nav(snapshot: PortfolioSnapshot) -> tuple[Decimal | None, list[Finding]]:
    """Use one explicitly labelled NAV or reconcile all supplied signed totals.

    Valuation totals are already extended by quantity by the host. Neither cost
    basis nor share count is a mark-to-market unit price here.
    """
    if any(row.unit == "nav" for row in snapshot.valuations or ()):
        return _labelled_value(snapshot, "nav")
    if snapshot.completeness != "complete" or snapshot.positions is None or snapshot.cash is None:
        return None, [
            _finding(
                "nav_components_unknown",
                "Complete positions and cash are required to reconcile NAV",
                snapshot.snapshot_id,
            )
        ]
    total = ZERO
    for row in snapshot.cash:
        if row.availability != Availability.KNOWN:
            return None, [
                _finding(
                    "cash_unknown", "Unknown cash cannot become zero NAV", snapshot.snapshot_id
                )
            ]
        if row.currency != snapshot.base_currency:
            return None, [
                _finding("nav_fx_missing", "Cash currency needs explicit FX", snapshot.snapshot_id)
            ]
        assert row.amount is not None
        total += row.amount
    for position in snapshot.positions:
        value = position.valuation
        if value is None or value.availability != Availability.KNOWN:
            return None, [
                _finding(
                    "position_value_unknown", "Position value is unknown", position.position_id
                )
            ]
        if (
            value.unit != "currency"
            or value.currency != position.currency
            or position.currency != snapshot.base_currency
        ):
            return None, [
                _finding(
                    "position_value_units_or_fx",
                    "Position total requires matching currency units and explicit FX",
                    position.position_id,
                )
            ]
        assert value.value is not None
        total += value.value
    return total, []


def _age_check(as_of: datetime | None, limit: int | None, now: datetime) -> str:
    if limit is None:
        return "not_configured"
    if as_of is None:
        return "unknown"
    age = (now - as_of).total_seconds()
    return "pass" if 0 <= age <= limit else "fail"


def analyze_portfolio(
    snapshot: PortfolioSnapshot, policy: PolicyConfig, *, now: datetime | None = None
) -> CalculationRecord:
    """Report supported values without turning payoff protection into capacity.

    Concentration is gross absolute marked issuer exposure / reported or
    reconciled positive NAV. Gross assignment funding is short-put strike
    notional before any long hedge, premium or margin offset. Call delivery
    obligations remain separately expressed in underlying shares.
    """
    clock = now if now is not None else datetime.now(UTC)
    parameters: dict[str, JsonValue] = {
        "snapshot": snapshot.model_dump(mode="json"),
        "policy": policy.model_dump(mode="json"),
        "policy_checks": {},
        "capacity_readiness": "unknown",
        "capacity_scope": "required_current_reported_broker_fields_only",
        "aggregate_complete": False,
        "gross_assignment_funding_by_currency": {},
        "unsupported_positions": [],
        "concentration_method": "gross_absolute_marked_issuer_value_divided_by_positive_nav",
        "risk_scope": "supported_marks_and_gross_contractual_obligations",
        "unmodeled_risks": [
            "correlation",
            "volatility",
            "early_assignment_timing",
            "broker_liquidation",
        ],
        "gross_funding_method": "short_put_strike_times_multiplier_times_absolute_contracts",
        "gross_funding_excludes": ["long_option_offsets", "premiums", "broker_margin"],
    }
    refs = (snapshot.snapshot_id,) + tuple(row.position_id for row in snapshot.positions or ())
    if clock.tzinfo is None or clock.utcoffset() is None:
        reason = "Portfolio evaluation clock must be timezone-aware"
        return _record(
            "analyze_portfolio",
            parameters,
            outputs={},
            units={},
            refs=refs,
            status="invalid_data",
            reason=reason,
            findings=(_finding("naive_evaluation_clock", reason, *refs),),
        )
    parameters["evaluated_at"] = clock.isoformat()
    coverage = match_coverage(snapshot)
    parameters["coverage_ref"] = coverage.calculation_id
    parameters["coverage"] = coverage.model_dump(mode="json")
    if coverage.status == "invalid_data":
        return _record(
            "analyze_portfolio",
            parameters,
            outputs={},
            units={},
            refs=refs,
            status="invalid_data",
            findings=coverage.findings,
            reason=coverage.reason,
        )
    findings = list(snapshot.findings) + list(coverage.findings)
    unsupported = [row for row in snapshot.positions or () if _unsupported(row)]
    parameters["unsupported_positions"] = [
        {"position_id": row.position_id, "instrument_type": row.contract.instrument_type}
        for row in unsupported
    ]
    outputs: dict[str, Decimal] = {}
    currencies: dict[str, str] = {}
    units: dict[str, str] = {}
    unknown = {
        key: Availability.UNKNOWN
        for key in (
            "nav",
            "gross_assignment_funding",
            "largest_issuer_weight",
            "broker_buying_power",
            "broker_maintenance_margin",
            "broker_excess_liquidity",
            "broker_initial_margin",
        )
    }
    complete = (
        snapshot.completeness == "complete"
        and snapshot.positions is not None
        and snapshot.cash is not None
        and all(balance.availability == Availability.KNOWN for balance in snapshot.cash)
        and not unsupported
    )
    if not complete:
        findings.append(
            _finding(
                "portfolio_aggregate_incomplete",
                "Partial or unmodeled exposure prevents a complete risk aggregate",
                snapshot.snapshot_id,
            )
        )
    position_currencies = {row.currency for row in snapshot.positions or ()}
    cash_currencies = {row.currency for row in snapshot.cash or ()}
    fx_missing = bool((position_currencies | cash_currencies) - {snapshot.base_currency})
    if fx_missing:
        complete = False
        findings.append(
            _finding(
                "portfolio_fx_missing",
                "No FX supplied; currency subtotals cannot become a base-currency aggregate",
                snapshot.snapshot_id,
            )
        )
    try:
        with localcontext(arithmetic_context()):
            nav, nav_findings = _snapshot_nav(snapshot)
            findings.extend(nav_findings)
            if nav is not None:
                outputs["nav"] = nav
                currencies["nav"] = snapshot.base_currency
                units["nav"] = "currency"
            funding: dict[str, Decimal] = {}
            issuer_funding: dict[str, dict[str, Decimal]] = {}
            call_shares: dict[str, Decimal] = {}
            issuer_values: dict[str, Decimal] = {}
            values_complete = complete
            for row in snapshot.positions or ():
                contract = row.contract
                if (
                    not _unsupported(row)
                    and contract.instrument_type == "option"
                    and row.quantity < 0
                ):
                    assert contract.multiplier is not None and contract.strike is not None
                    if contract.option_right == "put":
                        assert contract.underlying_id is not None
                        obligation = -row.quantity * contract.multiplier * contract.strike
                        funding[row.currency] = funding.get(row.currency, ZERO) + obligation
                        by_currency = issuer_funding.setdefault(contract.underlying_id, {})
                        by_currency[row.currency] = by_currency.get(row.currency, ZERO) + obligation
                    else:
                        assert contract.underlying_id is not None
                        call_shares[contract.underlying_id] = (
                            call_shares.get(contract.underlying_id, ZERO)
                            - row.quantity * contract.multiplier
                        )
                value = row.valuation
                if (
                    value is None
                    or value.availability != Availability.KNOWN
                    or value.unit != "currency"
                    or value.currency != row.currency
                    or row.currency != snapshot.base_currency
                ):
                    values_complete = False
                    continue
                assert value.value is not None
                issuer = contract.underlying_id or contract.instrument_id
                issuer_values[issuer] = issuer_values.get(issuer, ZERO) + abs(value.value)
            parameters["gross_assignment_funding_by_currency"] = {
                currency: str(value) for currency, value in sorted(funding.items())
            }
            parameters["gross_assignment_funding_by_issuer_and_currency"] = {
                issuer: {currency: str(value) for currency, value in sorted(values.items())}
                for issuer, values in sorted(issuer_funding.items())
            }
            parameters["short_call_delivery_shares_by_underlying"] = {
                issuer: str(value) for issuer, value in sorted(call_shares.items())
            }
            parameters["marked_issuer_exposure_base_currency"] = {
                issuer: str(value) for issuer, value in sorted(issuer_values.items())
            }
            # Funding may be known even when marks are absent, but not when a
            # missing/unsupported holding or FX prevents a complete obligation set.
            if complete:
                outputs["gross_assignment_funding"] = funding.get(snapshot.base_currency, ZERO)
                units["gross_assignment_funding"] = "currency"
                currencies["gross_assignment_funding"] = snapshot.base_currency
            if values_complete and nav is not None and nav > 0:
                outputs["largest_issuer_weight"] = max(issuer_values.values(), default=ZERO) / nav
                units["largest_issuer_weight"] = "ratio"
            if not values_complete or nav is None or nav <= 0:
                complete = False
                findings.append(
                    _finding(
                        "concentration_incomplete",
                        "Complete compatible marks and positive NAV are needed for concentration",
                        snapshot.snapshot_id,
                    )
                )
    except DecimalException:
        reason = "Portfolio values exceed supported Decimal arithmetic"
        return _record(
            "analyze_portfolio",
            parameters,
            outputs={},
            units={},
            refs=refs,
            status="invalid_data",
            reason=reason,
            findings=(_finding("invalid_portfolio_arithmetic", reason, *refs),),
        )
    parameters["aggregate_complete"] = complete
    checks: dict[str, JsonValue] = {}
    for field, output in (
        ("max_issuer_weight", "largest_issuer_weight"),
        ("max_gross_assignment_to_nav", "gross_assignment_funding"),
    ):
        limit = getattr(policy, field)
        if limit is None:
            checks[field] = "not_configured"
        elif output not in outputs or (
            field == "max_gross_assignment_to_nav" and (nav is None or nav <= 0)
        ):
            checks[field] = "unknown"
        else:
            try:
                with localcontext(arithmetic_context()):
                    metric = outputs[output]
                    if field == "max_gross_assignment_to_nav":
                        assert nav is not None
                        metric /= nav
                    checks[field] = "pass" if metric <= limit else "fail"
            except DecimalException:
                reason = "Policy ratio exceeds supported Decimal arithmetic"
                return _record(
                    "analyze_portfolio",
                    parameters,
                    outputs={},
                    units={},
                    refs=refs,
                    status="invalid_data",
                    reason=reason,
                    findings=(_finding("invalid_policy_arithmetic", reason, *refs),),
                )
    checks["max_snapshot_age_seconds"] = _age_check(
        snapshot.as_of, policy.max_snapshot_age_seconds, clock
    )
    capacity = snapshot.broker_capacity
    checks["max_capacity_age_seconds"] = _age_check(
        capacity.as_of if capacity else None, policy.max_capacity_age_seconds, clock
    )
    required_known = (
        bool(policy.required_capacity_fields)
        and capacity is not None
        and capacity.availability == Availability.KNOWN
        and not capacity.estimated
        and bool(capacity.source)
    )
    broker_fields: dict[str, JsonValue] = {}
    for name in ("buying_power", "maintenance_margin", "excess_liquidity", "initial_margin"):
        value = getattr(capacity, name) if capacity else None
        key = "broker_" + name
        if value is not None:
            broker_fields[name] = value.model_dump(mode="json")
        known = (
            value is not None
            and value.availability == Availability.KNOWN
            and value.unit == "currency"
            and value.currency == snapshot.base_currency
        )
        if known:
            assert value is not None and value.value is not None and value.currency is not None
            outputs[key] = value.value
            units[key] = "currency"
            currencies[key] = value.currency
        elif policy.required_capacity_fields and name in policy.required_capacity_fields:
            required_known = False
    parameters["broker_fields"] = broker_fields
    parameters["broker_capacity_estimated"] = capacity.estimated if capacity else None
    if (
        required_known
        and complete
        and checks["max_snapshot_age_seconds"] == "pass"
        and checks["max_capacity_age_seconds"] == "pass"
    ):
        parameters["capacity_readiness"] = "verified"
    else:
        findings.append(
            _finding(
                "capacity_unverified",
                "Required current broker evidence/freshness is absent or not configured; "
                "analytical values remain available",
                snapshot.snapshot_id,
            )
        )
    parameters["policy_checks"] = checks
    unknown = {key: state for key, state in unknown.items() if key not in outputs}
    return _record(
        "analyze_portfolio",
        parameters,
        outputs=outputs,
        units=units,
        currencies=currencies,
        availability=unknown,
        refs=refs,
        findings=tuple(findings),
        status="success" if complete else "unavailable",
        reason="Some portfolio aggregates are incomplete; see findings" if not complete else None,
    )
