"""Small direct-reference checks and a readable, evidence-backed final artifact.

Candidate selections are {slot, kind='candidate', idea_ref} or
{slot, kind='unavailable', reason}, with optional warnings. Financial records
remain the existing contracts. Checks never write records or invent judgments.
"""

from datetime import datetime
from decimal import Decimal, DecimalException
from typing import Any, cast

from pydantic import ValidationError

from portfolio_management.config import PolicyConfig
from portfolio_management.contracts import (
    CalculationRecord,
    CandidateStatus,
    ContractIdentity,
    Finding,
    IdeaRevision,
    PortfolioSnapshot,
    Position,
)
from portfolio_management.contracts.common import JsonValue
from portfolio_management.domain.coverage import match_coverage
from portfolio_management.domain.option_math import expiry_payoff, put_economics, roll_economics
from portfolio_management.domain.performance import performance
from portfolio_management.domain.portfolio import analyze_portfolio
from portfolio_management.types import TeamRequest, TeamResult
from portfolio_management.vault import EvidenceStore, InvalidRecord, RecordNotFound


def _finding(code: str, reason: str, *refs: str, error: bool = False) -> Finding:
    return Finding(
        code=code, severity="error" if error else "warning", reason=reason, affected_ids=refs
    )


def _selection(slot: dict[str, Any]) -> bool:
    if set(slot) - {"slot", "kind", "idea_ref", "reason", "warnings", "quote_refs"}:
        return False
    if type(slot.get("slot")) is not int or not 1 <= slot["slot"] <= 5:
        return False
    warnings = slot.get("warnings", [])
    if not isinstance(warnings, (list, tuple)) or not all(isinstance(w, str) for w in warnings):
        return False
    quote_refs = slot.get("quote_refs", [])
    if not isinstance(quote_refs, list) or not all(isinstance(r, str) for r in quote_refs):
        return False
    if slot.get("kind") == "unavailable":
        return bool(
            isinstance(slot.get("reason"), str)
            and slot["reason"].strip()
            and not slot.get("idea_ref")
        )
    ref = slot.get("idea_ref")
    return slot.get("kind") == "candidate" and isinstance(ref, str) and ref.startswith("idea:")


def _direct_refs(result: TeamResult, store: EvidenceStore) -> dict[str, dict[str, Any]]:
    """Resolve explicit output refs and fixed idea/quality support fields, once each.

    No arbitrary JSON, parent revisions, prose, or generic publication graph is walked.
    Validation reports missing refs separately.
    """
    refs = list(result.supporting_refs)
    refs += [
        str(s["idea_ref"])
        for s in result.candidate_slots
        if _selection(s) and s.get("kind") == "candidate"
    ]
    records: dict[str, dict[str, Any]] = {}
    for ref in dict.fromkeys(refs):
        try:
            records[ref] = store.get(ref)
        except (InvalidRecord, RecordNotFound):
            continue
    for ref, row in list(records.items()):
        if ref.startswith("idea:"):
            support = list(row["evidence_refs"]) + list(row["calculation_refs"])
            support += [row[k] for k in ("quality_ref", "snapshot_ref") if row.get(k)]
            for alternative in row["alternatives"]:
                support += alternative["evidence_refs"] + alternative["calculation_refs"]
            for child in support:
                try:
                    records[child] = store.get(child)
                except (InvalidRecord, RecordNotFound):
                    pass
    for ref, row in list(records.items()):
        if ref.startswith("quality:"):
            for child in row["evidence_refs"]:
                try:
                    records[child] = store.get(child)
                except (InvalidRecord, RecordNotFound):
                    pass
    return records


def _count(value: Any) -> int:
    number = Decimal(str(value))
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError("Whole contract quantity required")
    return int(number)


def _recalculate(row: dict[str, Any]) -> CalculationRecord:
    values, params = row["inputs"], row["parameters"]
    if row["formula"] == "put_economics":
        return put_economics(
            ContractIdentity.model_validate(params["contract"]),
            _count(values.get("quantity", params["requested_quantity"])),
            Decimal(str(values.get("premium", params["requested_premium"]))),
            Decimal(str(values.get("fees", params["requested_fees"]))),
            price_basis=params["price_basis"],
        )
    if row["formula"] == "roll_economics":
        amounts = values or params["requested_amounts"]
        return roll_economics(
            _count(values.get("quantity", params["requested_quantity"])),
            *(
                Decimal(str(amounts[key]))
                for key in ("multiplier", "original_credit", "close_debit", "new_credit", "fees")
            ),
            currency=params["currency"],
        )
    if row["formula"] == "expiry_payoff":
        return expiry_payoff(
            [Position.model_validate(p) for p in params["positions"]],
            {key: Decimal(str(value)) for key, value in params["requested_spots"].items()},
        )
    if row["formula"] == "match_coverage":
        return match_coverage(PortfolioSnapshot.model_validate(params["snapshot"]))
    if row["formula"] == "analyze_portfolio":
        return analyze_portfolio(
            PortfolioSnapshot.model_validate(params["snapshot"]),
            PolicyConfig.model_validate(params["policy"]),
            now=datetime.fromisoformat(params["evaluated_at"]),
        )
    if row["formula"] == "performance":
        return performance([PortfolioSnapshot.model_validate(s) for s in params["history"]])
    raise ValueError("Unknown recorded formula")


def _quote_freshness(
    rows: list[dict[str, Any]],
    result: TeamResult,
    idea_ref: str,
    contract: ContractIdentity | None,
    *,
    blocking: bool,
) -> tuple[bool, list[Finding]]:
    findings = []
    limit = result.owner_policy.max_quote_age_seconds
    if limit is None:
        findings.append(
            _finding(
                "quote_freshness_unconfigured",
                "Owner quote-age limit is unconfigured.",
                idea_ref,
                error=blocking,
            )
        )
    if not rows:
        findings.append(
            _finding(
                "quote_selection_unverified",
                "No supporting quote was selected.",
                idea_ref,
                error=blocking,
            )
        )
    for row in rows:
        content = row["content"]
        bound = (
            isinstance(content, dict)
            and contract is not None
            and (content.get("instrument_id") == contract.instrument_id)
        )
        prices = [
            content.get(k)
            for k in ("bid", "ask", "premium", "price")
            if isinstance(content, dict) and row["units"].get(k) == "currency_per_share"
        ]
        try:
            recognized = bound and any(
                Decimal(str(v)).is_finite() and Decimal(str(v)) >= 0 for v in prices
            )
        except DecimalException:
            recognized = False
        if not recognized:
            findings.append(
                _finding(
                    "quote_selection_unverified",
                    "Selected data lacks exact instrument and priced quote units.",
                    idea_ref,
                    error=blocking,
                )
            )
        if row["delay_state"] != "live":
            findings.append(
                _finding(
                    "quote_delay_unverified",
                    "EOD, delayed or unknown-delay evidence is not a live quote.",
                    idea_ref,
                    error=blocking,
                )
            )
        if not row["source_timestamp"]:
            findings.append(
                _finding(
                    "quote_time_unknown", "Quote source time is unknown.", idea_ref, error=blocking
                )
            )
        elif result.completed_at is None:
            findings.append(
                _finding(
                    "quote_cutoff_unknown",
                    "Host validation cutoff is unknown.",
                    idea_ref,
                    error=blocking,
                )
            )
        else:
            age = (
                result.completed_at - datetime.fromisoformat(row["source_timestamp"])
            ).total_seconds()
            if age < 0:
                findings.append(
                    _finding(
                        "quote_time_in_future",
                        "Quote source time is after host cutoff.",
                        idea_ref,
                        error=blocking,
                    )
                )
            elif limit is not None and age > limit:
                findings.append(
                    _finding(
                        "quote_stale",
                        "Quote source age exceeds owner limit; "
                        "a recent fetch does not refresh the source.",
                        idea_ref,
                        error=blocking,
                    )
                )
    return bool(rows) and not findings, findings


def _quote_supports_put(quote: dict[str, Any], calculation: dict[str, Any]) -> bool:
    """Bind the recorded premium to its declared, selected quote field."""
    basis = calculation["parameters"].get("price_basis")
    content = quote["content"]
    if (
        basis not in {"bid", "ask", "premium", "price"}
        or not isinstance(content, dict)
        or content.get("instrument_id") != calculation["parameters"]["contract"]["instrument_id"]
        or quote["units"].get(basis) != "currency_per_share"
    ):
        return False
    try:
        price = Decimal(str(content.get(basis)))
        return price.is_finite() and price == Decimal(calculation["inputs"]["premium"])
    except (DecimalException, KeyError):
        return False


def validate_result(
    result: TeamResult, store: EvidenceStore, request: TeamRequest
) -> list[Finding]:
    """Report shape, direct support and reproducibility; do not supply investment views."""
    findings: list[Finding] = []
    slots = result.candidate_slots
    if request.mode in {"portfolio", "combined"} and request.portfolio is None:
        findings.append(
            _finding(
                "portfolio_unavailable",
                "Portfolio snapshot unavailable; "
                "holdings, broker capacity and risk limits cannot be inferred.",
            )
        )
    if request.mode in {"discovery", "combined"} and (
        len(slots) != 5 or {s.get("slot") for s in slots if _selection(s)} != set(range(1, 6))
    ):
        findings.append(
            _finding(
                "candidate_slots_missing",
                "Discovery requires five numbered candidate or unavailable slots.",
                error=True,
            )
        )
    if not result.summary.strip() and not result.portfolio_section and not slots:
        findings.append(_finding("empty_result", "No substantive result was supplied.", error=True))
    narrative = (
        result.summary + "\n" + result.report_markdown + "\n" + (result.portfolio_section or "")
    )
    if (
        request.mode != "education"
        and not any(s.get("kind") == "candidate" for s in slots)
        and CandidateStatus.READY.value.casefold() in narrative.casefold()
    ):
        findings.append(
            _finding(
                "unsupported_readiness_claim",
                "Narrative readiness has no supported structured candidate.",
                error=True,
            )
        )
    records = _direct_refs(result, store)
    expected = set(result.supporting_refs)
    companies: set[str] = set()
    for slot in slots:
        if not _selection(slot):
            findings.append(
                _finding("invalid_candidate_slot", "Malformed candidate selection.", error=True)
            )
            continue
        if slot["kind"] == "unavailable":
            continue
        ref = str(slot["idea_ref"])
        expected.add(ref)
        if ref not in records:
            continue
        idea = IdeaRevision.model_validate(records[ref])
        if idea.company_id in companies:
            findings.append(
                _finding(
                    "duplicate_company", "Candidates must be distinct companies.", ref, error=True
                )
            )
        companies.add(idea.company_id)
        support = set(idea.evidence_refs) | set(idea.calculation_refs)
        support.update(r for r in (idea.quality_ref, idea.snapshot_ref) if r)
        expected.update(support)
        reference_kinds = [(r, "evidence") for r in idea.evidence_refs]
        reference_kinds += [(r, "calculation") for r in idea.calculation_refs]
        reference_kinds += [
            (r, kind)
            for r, kind in ((idea.quality_ref, "quality"), (idea.snapshot_ref, "snapshot"))
            if r
        ]
        for alternative in idea.alternatives:
            expected.update(alternative.evidence_refs + alternative.calculation_refs)
            reference_kinds += [(r, "evidence") for r in alternative.evidence_refs]
            reference_kinds += [(r, "calculation") for r in alternative.calculation_refs]
        if any(not r.startswith(kind + ":") for r, kind in reference_kinds):
            findings.append(
                _finding(
                    "invalid_support_reference",
                    "Idea support uses an incorrect typed reference.",
                    ref,
                    error=True,
                )
            )
            continue
        if not idea.evidence_refs or not idea.quality_ref:
            findings.append(
                _finding(
                    "candidate_support_missing",
                    "Company claims require source and quality support.",
                    ref,
                    error=True,
                )
            )
        quality = records.get(idea.quality_ref or "", {})
        if quality and quality.get("company_id") != idea.company_id:
            findings.append(
                _finding(
                    "quality_company_mismatch",
                    "Quality support belongs to another company.",
                    ref,
                    error=True,
                )
            )
        if quality:
            expected.update(quality["evidence_refs"])
            if not quality["evidence_refs"]:
                findings.append(
                    _finding(
                        "candidate_support_missing",
                        "Quality assessment has no source support.",
                        ref,
                        error=True,
                    )
                )
        calculations = [records[r] for r in idea.calculation_refs if r in records]
        matching: list[dict[str, Any]] = []
        if idea.contract is not None:
            matching = [
                c
                for c in calculations
                if c["formula"] == "put_economics"
                and c["parameters"].get("contract")
                == idea.contract.model_dump(mode="json", exclude={"raw"})
            ]
            if idea.contract.option_right == "put" and not matching:
                findings.append(
                    _finding(
                        "contract_economics_missing",
                        "Exact put terms lack matching recorded economics.",
                        ref,
                        error=True,
                    )
                )
        quote_refs = cast(list[str], slot.get("quote_refs", []))
        if any(r not in idea.evidence_refs for r in quote_refs):
            findings.append(
                _finding(
                    "invalid_quote_reference",
                    "Selected quote must belong to this idea's evidence refs.",
                    ref,
                    error=True,
                )
            )
        source_rows = [records[r] for r in quote_refs if r in idea.evidence_refs and r in records]
        current, freshness_findings = _quote_freshness(
            source_rows, result, ref, idea.contract, blocking=idea.status == CandidateStatus.READY
        )
        findings.extend(freshness_findings)
        if not current:
            findings.append(
                _finding(
                    "evidence_not_current",
                    "Source time/delay does not establish a current executable quote.",
                    ref,
                )
            )
        if idea.snapshot_ref and request.portfolio is not None:
            if records.get(idea.snapshot_ref) != request.portfolio.model_dump(mode="json"):
                findings.append(
                    _finding(
                        "snapshot_mismatch",
                        "Candidate snapshot differs from the host snapshot.",
                        ref,
                        error=True,
                    )
                )
        if idea.status == CandidateStatus.READY:
            if (
                idea.contract is not None
                and idea.contract.option_right == "put"
                and not (
                    matching
                    and all(any(_quote_supports_put(q, c) for q in source_rows) for c in matching)
                )
            ):
                findings.append(
                    _finding(
                        "quote_economics_unverified",
                        "Recorded put premium/basis is not supported by the selected quote.",
                        ref,
                        error=True,
                    )
                )
            portfolio_checks = [c for c in calculations if c["formula"] == "analyze_portfolio"]
            capacity_verified = bool(portfolio_checks) and all(
                c["parameters"].get("capacity_readiness") == "verified"
                and c["parameters"].get("policy") == result.owner_policy.model_dump(mode="json")
                and request.portfolio is not None
                and c["parameters"].get("snapshot") == request.portfolio.model_dump(mode="json")
                and all(
                    value == "pass" for value in c["parameters"].get("policy_checks", {}).values()
                )
                for c in portfolio_checks
            )
            if request.portfolio is not None and result.completed_at is not None:
                at_cutoff = analyze_portfolio(
                    request.portfolio, result.owner_policy, now=result.completed_at
                )
                cutoff_parameters = at_cutoff.parameters
                cutoff_checks = cast(dict[str, Any], cutoff_parameters["policy_checks"])
                fresh_at_cutoff = cutoff_parameters["capacity_readiness"] == "verified" and all(
                    value == "pass" for value in cutoff_checks.values()
                )
                if not fresh_at_cutoff:
                    findings.append(
                        _finding(
                            "portfolio_freshness_unverified",
                            "Host completion cutoff requires current snapshot/capacity checks: "
                            f"snapshot={cutoff_checks.get('max_snapshot_age_seconds')}; "
                            f"capacity={cutoff_checks.get('max_capacity_age_seconds')}. "
                            "The original calculation audit remains unchanged.",
                            ref,
                            error=True,
                        )
                    )
                capacity_verified = capacity_verified and fresh_at_cutoff
            else:
                capacity_verified = False
            if (
                not current
                or not capacity_verified
                or request.portfolio is None
                or not idea.snapshot_ref
                or not idea.contract
                or quality.get("conclusion") != "qualified"
                or any(c["status"] != "success" for c in calculations)
            ):
                findings.append(
                    _finding(
                        "readiness_unverified",
                        "Readiness blocked: current quote, exact economics, qualified "
                        "quality, supplied snapshot and configured capacity/risk checks "
                        "must all be supported; absent limits are unconfigured.",
                        ref,
                        error=True,
                    )
                )
    # Focused outputs may name an idea directly rather than through discovery slots.
    for ref, row in records.items():
        if ref.startswith(("idea:", "quality:")):
            expected.update(row["evidence_refs"])
        if ref.startswith("idea:"):
            expected.update(row["calculation_refs"])
            expected.update(row[k] for k in ("quality_ref", "snapshot_ref") if row.get(k))
            for alternative in row["alternatives"]:
                expected.update(alternative["evidence_refs"] + alternative["calculation_refs"])
    for ref in sorted(expected - records.keys()):
        findings.append(
            _finding(
                "unresolved_reference",
                "Supporting reference is absent or invalid.",
                ref,
                error=True,
            )
        )
    for ref, row in records.items():
        findings.extend(Finding.model_validate(f) for f in row.get("findings", ()))
        if ref.startswith("evidence:"):
            fetched = datetime.fromisoformat(row["fetched_at"])
            source = (
                datetime.fromisoformat(row["source_timestamp"]) if row["source_timestamp"] else None
            )
            if source is not None and source > fetched:
                findings.append(
                    _finding(
                        "evidence_time_inconsistent",
                        "Source time is after fetch time.",
                        ref,
                        error=True,
                    )
                )
            if result.completed_at is not None and (
                fetched > result.completed_at
                or (source is not None and source > result.completed_at)
            ):
                findings.append(
                    _finding(
                        "evidence_time_in_future",
                        "Evidence timestamp is after host cutoff.",
                        ref,
                        error=True,
                    )
                )
        if not ref.startswith("calculation:"):
            continue
        try:
            recalculated = _recalculate(row).model_dump(mode="json")
            keys = ("status", "inputs", "outputs", "units", "currencies", "availability")
            critical_params = (
                "capacity_readiness",
                "policy_checks",
                "funding_or_margin_verified",
                "coverage_allocations",
                "matched_strike_gap_by_currency",
            )
            mismatch = any(row[k] != recalculated[k] for k in keys) or any(
                row["parameters"].get(k) != recalculated["parameters"].get(k)
                for k in critical_params
            )
        except (KeyError, TypeError, ValueError, DecimalException, ValidationError):
            mismatch = True
        if mismatch:
            findings.append(
                _finding(
                    "calculation_mismatch",
                    "Recorded result does not reproduce from its declared inputs; "
                    "figures are unverified and cannot establish readiness.",
                    ref,
                    error=True,
                )
            )
    return findings


def report_records(result: TeamResult, store: EvidenceStore) -> dict[str, dict[str, JsonValue]]:
    """Bounded projections for rendering; never copy source documents/provider bodies."""
    projected: dict[str, dict[str, JsonValue]] = {}
    for ref, row in _direct_refs(result, store).items():
        if ref.startswith("evidence:"):
            fields = (
                "source",
                "fetched_at",
                "source_timestamp",
                "source_time_availability",
                "timezone_state",
                "delay_state",
                "delay_seconds",
                "coverage",
                "source_url",
            )
            projection = {key: row[key] for key in fields}
            content = row["content"]
            quote_fields = (
                "price",
                "premium",
                "bid",
                "ask",
                "spread",
                "volume",
                "open_interest",
                "dte",
                "valuation",
                "currency",
                "instrument_id",
            )
            projection["quote"] = {
                key: content[key]
                for key in quote_fields
                if isinstance(content, dict)
                and key in content
                and isinstance(content[key], (str, int, type(None)))
            }
        elif ref.startswith("calculation:"):
            projection = {
                key: row[key]
                for key in (
                    "formula",
                    "status",
                    "inputs",
                    "outputs",
                    "units",
                    "currencies",
                    "availability",
                    "computed_at",
                    "reason",
                )
            }
            fields = (
                "price_basis",
                "return_denominator",
                "annualization",
                "capacity_readiness",
                "policy_checks",
                "coverage_allocations",
                "matched_strike_gap_by_currency",
                "gross_assignment_funding_by_currency",
                "unsupported_positions",
            )
            projection["parameters"] = {
                key: row["parameters"][key] for key in fields if key in row["parameters"]
            }
        elif ref.startswith("snapshot:"):
            projection = {key: row[key] for key in ("snapshot_id", "source", "as_of", "fetched_at")}
        else:
            projection = dict(row)
            if projection.get("contract"):
                projection["contract"] = {
                    k: v for k, v in projection["contract"].items() if k != "raw"
                }
            for alternative in projection.get("alternatives", []):
                if alternative.get("contract"):
                    alternative["contract"] = {
                        k: v for k, v in alternative["contract"].items() if k != "raw"
                    }
        projected[ref] = projection
    return projected


def _terms(contract: dict[str, Any]) -> str:
    fields = (
        "instrument_id",
        "instrument_type",
        "expiry",
        "option_right",
        "strike",
        "multiplier",
        "currency",
        "deliverable",
        "adjustment",
    )
    return "; ".join(f"{key}={contract[key]}" for key in fields if contract.get(key) is not None)


def render_result(result: TeamResult) -> str:
    """Render model judgment alongside visibly authoritative integrity labels and records."""
    records = cast(dict[str, dict[str, Any]], result.supporting_records)
    slots = cast(tuple[dict[str, Any], ...], result.candidate_slots)
    rows = ["# Team response", "", f"Mode: {result.mode}; completion: {result.completion_reason}"]
    if result.started_at:
        rows += [
            f"Started: {result.started_at.isoformat()}; completed: "
            f"{result.completed_at.isoformat() if result.completed_at else 'unknown'}"
        ]
    if any(f.severity == "error" for f in result.findings):
        rows += [
            "",
            "**Integrity checks blocked unsupported claims. Do not execute now — unverified.**",
            "Saved content below is subject to these findings.",
        ]
    rows += ["", result.summary]
    if result.report_markdown and result.report_markdown.strip() != result.summary.strip():
        rows += ["", "## Lead narrative", "", result.report_markdown]
    if result.candidate_slots:
        rows += ["", "## Discovery", "", "Candidates are alternatives, not a basket instruction."]
    for slot in slots:
        number = slot.get("slot", "?")
        if not _selection(slot):
            rows += [
                "",
                f"### Slot {number} — unavailable",
                "",
                "Malformed candidate selection; see validation findings.",
            ]
            continue
        ref = str(slot.get("idea_ref", ""))
        idea = records.get(ref)
        if slot.get("kind") == "unavailable" or not idea:
            rows += [
                "",
                f"### Slot {number} — unavailable",
                "",
                str(slot.get("reason") or "No supported candidate record is available."),
            ]
            rows += ["Warning: " + str(w) for w in slot.get("warnings", [])]
            continue
        support = set(idea.get("evidence_refs", []) + idea.get("calculation_refs", []))
        support.update(r for r in (idea.get("quality_ref"), idea.get("snapshot_ref")) if r)
        quality = records.get(idea.get("quality_ref", ""), {})
        support.update(quality.get("evidence_refs", []))
        for alternative in idea.get("alternatives", []):
            support.update(alternative["evidence_refs"] + alternative["calculation_refs"])
        blocked = any(
            f.severity == "error"
            and (
                not f.affected_ids
                or ref in f.affected_ids
                or any(r in f.affected_ids for r in support)
            )
            for f in result.findings
        )
        status = (
            "Do not execute now — unverified (integrity blocked)" if blocked else idea["status"]
        )
        rows += ["", f"### Slot {number}: {idea['company_id']} — {status}", "", f"Idea: {ref}"]
        if blocked:
            rows += [f"Lead-recorded status (unverified claim): {idea['status']}"]
        rows += [str(idea["thesis"]), "Countercase: " + str(idea["countercase"])]
        if idea.get("contract"):
            rows += ["Terms: " + _terms(idea["contract"])]
        rows += ["Reason: " + str(reason) for reason in idea.get("reasons", [])]
        rows += ["Unknown: " + str(reason) for reason in idea.get("unknowns", [])]
        for alternative in idea.get("alternatives", []):
            rows += [
                "Alternative: "
                + str(alternative["description"])
                + "; "
                + str(alternative["rationale"])
            ]
        rows += ["Warning: " + str(w) for w in slot.get("warnings", [])]
    if (
        result.portfolio_section is not None
        or result.portfolio_snapshot is not None
        or result.mode in {"portfolio", "combined"}
    ):
        rows += [
            "",
            "## Portfolio",
            "",
            result.portfolio_section or "Portfolio judgment unavailable.",
        ]
        if result.portfolio_snapshot is None:
            rows += ["Portfolio snapshot unavailable; holdings and broker capacity are unknown."]
    snapshot = result.portfolio_snapshot
    if snapshot is not None:
        rows += [
            "",
            f"Snapshot {snapshot.snapshot_id}: as-of {snapshot.as_of.isoformat()}; "
            f"fetched {snapshot.fetched_at.isoformat()}; {snapshot.completeness}.",
            "Broker capacity and owner risk limits remain unknown/unconfigured unless "
            "explicitly verified by recorded checks.",
        ]
        if snapshot.positions is None:
            rows += ["Holdings: unknown (collection absent)."]
        elif not snapshot.positions:
            rows += ["Holdings: supplied empty collection."]
        for position in snapshot.positions or ():
            contract = position.contract
            unsupported = contract.instrument_type not in {"stock", "option"} or bool(
                contract.deliverable or contract.adjustment
            )
            rows += [
                f"- {position.position_id}: quantity={position.quantity}; "
                f"{_terms(contract.model_dump(mode='json'))}"
                + ("; unsupported instrument/deliverable" if unsupported else "")
            ]
            rows += ["  Warning: " + finding.reason for finding in position.findings]
        for cash in snapshot.cash or ():
            amount = cash.amount if cash.amount is not None else cash.availability
            rows += [f"Cash {cash.currency}: {amount}"]
        rows += ["Warning: " + finding.reason for finding in snapshot.findings]
    if result.supporting_records:
        rows += ["", "## Supporting records"]
    for ref, record in records.items():
        rows += ["", f"### {ref}"]
        if ref.startswith("evidence:"):
            rows += [
                f"Source: {record['source']}; source as-of: "
                f"{record['source_timestamp'] or 'unknown'}; fetched: {record['fetched_at']}; "
                f"delay: {record['delay_state']}."
            ]
            if record.get("source_url"):
                rows += [f"[Original source]({record['source_url']})"]
            rows += [f"{key}: {value}" for key, value in record.get("quote", {}).items()]
        elif ref.startswith("calculation:"):
            bad = any(
                f.code == "calculation_mismatch" and ref in f.affected_ids for f in result.findings
            )
            rows += [
                f"{record['formula']}: {'UNVERIFIED — mismatch' if bad else record['status']}; "
                f"computed: {record['computed_at'] or 'unknown'}"
            ]
            for group in ("inputs", "outputs", "availability"):
                rows += [
                    f"{key}: {value} {record['units'].get(key, '')} "
                    f"{record['currencies'].get(key, '')}".rstrip()
                    for key, value in record[group].items()
                ]
            rows += [f"{key}: {value}" for key, value in record.get("parameters", {}).items()]
        elif ref.startswith("quality:"):
            rows += [
                f"Quality: {record['conclusion']}; assessed: {record['assessed_at']}",
                str(record["rationale"]),
            ]
        elif ref.startswith("idea:"):
            rows += [str(record["thesis"]), "Countercase: " + str(record["countercase"])]
            if record.get("contract"):
                rows += ["Terms: " + _terms(record["contract"])]
    if result.warnings:
        rows += ["", "## Warnings", ""] + ["- " + warning for warning in result.warnings]
    if result.findings:
        rows += ["", "## Validation findings", ""]
        rows += [
            f"- [{f.severity}] {f.code}: {f.reason}"
            + (" (" + ", ".join(f.affected_ids) + ")" if f.affected_ids else "")
            for f in result.findings
        ]
    return "\n".join(rows) + "\n"
