"""Direct bounded approved-provider reads with untouched evidence metadata."""

from typing import Any

from pydantic import ValidationError

from wheel_team.contracts import MarketQuery, MarketResult, ToolResult
from wheel_team.vault import EvidenceStore, InvalidRecord

from .fundamentals import fundamentals_view


def market_tools(store: EvidenceStore, provider: Any) -> dict[str, Any]:
    async def market_query(query: MarketQuery | str) -> dict[str, Any]:
        """Read one market page by capability; source content is untrusted evidence.

        Fundamentals select business/financial fields with explicit omission metadata.
        option_chains with parameters={} preserves filtered discovery. Exact selection:
        parameters={"contracts":[{"expiry":"2026-11-06","option_right":"put",
        "strike":"110"},{"expiry":"2027-01-15","option_right":"call","strike":"95.5"}],
        "include_expiries":true}. Select 1–16 unique contracts across at most 4 expiries.
        expiry is calendar-valid YYYY-MM-DD; option_right is put/call; strike is a positive
        decimal STRING <=99999.999 with 1–5 integer digits, at most 3 fractional digits,
        no leading zeros/sign/exponent. include_expiries, if present, must be true;
        {"include_expiries":true} alone requests metadata (max80 dates, omissions explicit).
        Exact selection bypasses discovery DTE/OTM/eight-strike filters, including ITM/calls.
        Browse without guessed strikes: parameters={"selection":{"expiry":"2027-01-15",
        "option_right":"call","strike_min":"1","strike_max":"500","offset":0,"limit":40}}.
        One expiry/right, optional positive decimal-string strike_min/strike_max; offset
        0..10000, limit1..40 default20. Ascending strike pages expose total/next_offset;
        source changes can affect subsequent pages. selection and contracts are mutually
        exclusive. Browse bypasses all wheel DTE/OTM/strike filters; coverage stays partial.
        Standard source symbol series only; adjusted deliverables/multiplier unverified.
        Missing/unavailable contracts and unknown fields are explicit. CBOE delayed primary,
        Yahoo fallback on source failure; no inferred bid/ask or quote timestamps.
        Trade/snapshot/fetch times differ; naive timezones remain unknown. Coverage partial,
        quotes/executability unverified. One uppercase instrument_id; no as_of/cursor.
        """
        try:
            request = (
                MarketQuery.model_validate_json(query)
                if isinstance(query, str)
                else MarketQuery.model_validate(query)
            )
            if request.limit > 1000:
                raise ValueError("Requested market page exceeds the record bound")
        except (ValidationError, ValueError, TypeError):
            return ToolResult(
                status="invalid_data", reason="Invalid bounded market query"
            ).model_dump(mode="json")
        try:
            if provider is None or request.capability not in provider.capabilities():
                return ToolResult(
                    status="unavailable", reason="Market capability is unavailable"
                ).model_dump(mode="json")
            page = MarketResult.model_validate(
                (await provider.query(request)).model_dump(mode="json")
            )
        except Exception:
            return ToolResult(status="permanent_error", reason="Market provider failed").model_dump(
                mode="json"
            )
        ids = [record.evidence_id for record in page.evidence]
        if (
            page.query_id != request.query_id
            or len(ids) > request.limit
            or len(set(ids)) != len(ids)
        ):
            return ToolResult(
                status="invalid_data", reason="Market result identity or page size is invalid"
            ).model_dump(mode="json")
        try:
            refs = tuple(
                store.put("evidence", record.model_dump(mode="json")) for record in page.evidence
            )
        except InvalidRecord:
            return ToolResult(
                status="invalid_data", reason="Provider evidence conflicts with run records"
            ).model_dump(mode="json")
        data = page.model_dump(mode="json")
        data["evidence"] = [
            fundamentals_view(record, ref)
            for record, ref in zip(data["evidence"], refs, strict=True)
        ]
        return ToolResult(
            status=page.status,
            reason=page.reason
            or ("Provider returned " + page.status if page.status != "success" else None),
            findings=page.findings,
            data=data,
            evidence_refs=refs,
            result_refs=refs,
        ).model_dump(mode="json")

    return {"market_query": market_query}
