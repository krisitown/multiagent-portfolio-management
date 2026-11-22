"""Accept supplied public snapshots without accessing a private host or broker."""

from portfolio_management.contracts import Finding, JsonValue, PortfolioSnapshot


def import_snapshot(payload: dict[str, JsonValue]) -> PortfolioSnapshot:
    """Validate a detached immutable snapshot and diagnose partial/unmodeled data.

    Position valuation means the supplied total signed position value, not a
    unit price. Missing collections are not converted to an empty account.
    CashFlow amounts use signed account cash: deposits are nonnegative and
    withdrawals nonpositive. Hosts with positive withdrawal magnitudes must
    explicitly normalize their format; performance rejects conflicting signs.
    """
    snapshot = PortfolioSnapshot.model_validate(payload)
    rows = snapshot.positions
    if rows is not None and len({row.position_id for row in rows}) != len(rows):
        raise ValueError("Duplicate position IDs would double count portfolio holdings")
    findings = list(snapshot.findings)
    missing = [
        name for name in ("positions", "cash", "broker_capacity") if getattr(snapshot, name) is None
    ]
    if missing:
        findings.append(
            Finding(
                code="snapshot_fields_unknown",
                severity="warning",
                affected_ids=(snapshot.snapshot_id,),
                reason="Host did not supply: " + ", ".join(missing),
            )
        )
    for row in rows or ():
        contract = row.contract
        if (
            contract.instrument_type not in {"stock", "option"}
            or contract.deliverable
            or contract.adjustment
        ):
            findings.append(
                Finding(
                    code="unsupported_holding",
                    severity="warning",
                    affected_ids=(row.position_id,),
                    reason=f"Preserved unmodeled holding: {contract.instrument_type}",
                )
            )
    completeness = snapshot.completeness
    if completeness == "complete" and (snapshot.positions is None or snapshot.cash is None):
        completeness = "partial"
    return snapshot.model_copy(update={"findings": tuple(findings), "completeness": completeness})
