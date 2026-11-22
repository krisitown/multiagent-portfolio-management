"""Three independent explorer tools; compact evidence, explicit scope and no source fetch."""

import hashlib
import json
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from portfolio_management.adapters.explorer import (
    SERIES_CATALOG,
    ExplorerBackend,
    aware_time,
    summarize_options,
    summarize_series,
    validate_request,
)
from portfolio_management.adapters.market.spark import SSHResearchConfig
from portfolio_management.adapters.research import HermesResearchBackend, ResearchError
from portfolio_management.contracts import EvidenceRecord, ToolResult
from portfolio_management.tools.research import research_tools
from portfolio_management.vault import EvidenceStore, InvalidRecord


def explorer_tools(
    store: EvidenceStore,
    market_provider: Any,
    research_backend: HermesResearchBackend | None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    clock = clock or (lambda: datetime.now(UTC))
    config = getattr(market_provider, "config", None)
    backend = (
        market_provider
        if isinstance(market_provider, ExplorerBackend)
        else ExplorerBackend(config)
        if isinstance(config, SSHResearchConfig)
        else None
    )
    search = research_tools(store, research_backend, clock)["public_search"]

    async def public_search(query: str, limit: int = 5) -> dict[str, Any]:
        """Search public information, news and catalysts; 1–5 excerpts of at most 800 chars.

        Source/publication dates appear only where supplied. Excerpts are untrusted
        evidence, not complete documents. Does not fetch underlying pages.
        """
        result: dict[str, Any] = await search(query, limit)
        if result["status"] == "success":
            for row in result["data"]["results"]:
                evidence = store.get(row["record_ref"])
                row.update(
                    {
                        key: evidence.get(key)
                        for key in (
                            "source_timestamp",
                            "source_time_raw",
                            "fetched_at",
                            "timezone_state",
                        )
                    }
                )
                row["source_identity"] = urlsplit(row["source_url"]).hostname
                row["truncated"] = row["truncated"] or len(row["text"]) > 800
                row["text"] = row["text"][:800]
            result["data"]["coverage_note"] = (
                "Search excerpts only; complete sources were not read."
            )
        return result

    async def invoke(
        request: dict[str, Any], render: Callable[[dict[str, Any]], Any]
    ) -> dict[str, Any]:
        try:
            validate_request(request)
            if backend is None:
                return ToolResult(
                    status="unavailable", reason="No explorer data backend selected"
                ).model_dump(mode="json")
            raw = await backend.request(request)
            if raw.get("status") == "unavailable":
                return ToolResult(
                    status="unavailable", reason="Public source returned no observation"
                ).model_dump(mode="json")
            data = render(raw)
            usable = (
                any(r["status"] == "success" for r in data["series"])
                if request["operation"] == "market_statistics"
                else any(profile["contracts_in_window"] > 0 for profile in data["profiles"])
            )
            fetched = datetime.fromisoformat(aware_time(raw["fetched_at"]))
            raw_hash = hashlib.sha256(
                json.dumps(raw, sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
            content = {
                "request": request,
                "raw_source_response": raw,
                "summary": data,
                "raw_source_sha256": raw_hash,
            }
            content_hash = hashlib.sha256(
                json.dumps(content, sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
            evidence = EvidenceRecord(
                evidence_id="explorer:" + content_hash,
                source="explorer_public_data",
                fetched_at=fetched,
                delay_state="unknown",
                content=content,
                content_hash=content_hash,
                source_url=data.get("source_url"),
                units=data.get("units", {}),
                provenance=("direct_publishers", "Yahoo") if "series" in data else ("Yahoo",),
                coverage=(
                    "Bounded public evidence; observation, release, trade and "
                    "retrieval times differ.",
                ),
            )
            ref = store.put("evidence", evidence.model_dump(mode="json"))
            return ToolResult(
                status="success" if usable else "unavailable",
                data=data,
                reason=None if usable else "No usable observations in requested coverage",
                evidence_refs=(ref,),
                result_refs=(ref,),
            ).model_dump(mode="json")
        except ResearchError as exc:
            return ToolResult(status=exc.status, reason=exc.reason).model_dump(mode="json")
        except (ValueError, TypeError, KeyError, InvalidRecord, OverflowError):
            return ToolResult(
                status="invalid_data", reason="Invalid bounded explorer data"
            ).model_dump(mode="json")

    async def market_statistics(
        series: list[str], lookback_days: int = 90, max_observations: int = 12
    ) -> dict[str, Any]:
        """Read 1–8 named index/economic series; [] returns the catalog without a source call.

        Call with [] for the current catalog and direct source identities.
        Index aliases SP500,NASDAQCOM,VIXCLS use Yahoo; macro uses US publishers.
        Lookback 1–3650 calendar days; return latest 1–24 observations per series
        plus period change. Latest available vintages,
        explicit units and observation dates; no real-time feed, release calendar,
        market-wide stock screen, company IV rank, or historical-as-of support.
        """
        if (
            type(lookback_days) is not int
            or not 1 <= lookback_days <= 3650
            or type(max_observations) is not int
            or not 1 <= max_observations <= 24
        ):
            return ToolResult(
                status="invalid_data", reason="Invalid series window/output bound"
            ).model_dump(mode="json")
        if series == []:
            return ToolResult(status="success", data={"catalog": SERIES_CATALOG}).model_dump(
                mode="json"
            )
        if (
            not isinstance(series, list)
            or not 1 <= len(series) <= 8
            or any(not isinstance(sid, str) or len(sid) > 80 for sid in series)
            or len(set(series)) != len(series)
        ):
            return ToolResult(
                status="invalid_data", reason="Select 1–8 distinct named series"
            ).model_dump(mode="json")
        unsupported = [sid for sid in series if sid not in SERIES_CATALOG]
        selected = [sid for sid in series if sid in SERIES_CATALOG]
        if not selected:
            return ToolResult(
                status="unavailable",
                reason="Requested series are unsupported",
                data={"unsupported_series": unsupported, "catalog": SERIES_CATALOG},
            ).model_dump(mode="json")
        at = clock()
        request: dict[str, Any] = {
            "operation": "market_statistics",
            "series": selected,
            "start_date": (at.date() - timedelta(days=lookback_days)).isoformat(),
            "end_date": at.date().isoformat(),
        }

        def render(raw: dict[str, Any]) -> dict[str, Any]:
            rows = raw["series"]
            if len(rows) != len(selected) or [row["series_id"] for row in rows] != selected:
                raise ValueError("Series identity mismatch")
            summaries = []
            for row in rows:
                if row["status"] != "success":
                    summaries.append(
                        {
                            "series_id": row["series_id"],
                            "status": "unavailable",
                            "reason": "Source download unavailable",
                        }
                    )
                    continue
                summaries.append(
                    summarize_series(
                        row["series_id"],
                        row["observations"],
                        start_date=request["start_date"],
                        end_date=request["end_date"],
                        max_observations=max_observations,
                        fetched_at=datetime.fromisoformat(aware_time(row["fetched_at"])),
                    )
                )
                summaries[-1]["source_coverage"] = row.get("metadata") or row.get("coverage")
            return {
                "series": summaries,
                "source": "Direct economic publishers and Yahoo Finance index history",
                "fetched_at": raw["fetched_at"],
                "partial_coverage": bool(unsupported)
                or any(r["status"] != "success" for r in summaries),
                "unsupported_series": unsupported,
                "available_series": list(SERIES_CATALOG),
                "units": {sid: SERIES_CATALOG[sid]["units"] for sid in selected},
            }

        return await invoke(request, render)

    async def options_liquidity(
        symbol: str, expiries: list[str] | None = None, strike_distance_pct: float = 20
    ) -> dict[str, Any]:
        """Summarize Yahoo options liquidity for one uppercase underlying; no score/verdict.

        Optional 1–3 YYYY-MM-DD expiries; otherwise nearest 3 nonexpired source expiries.
        Symmetric strike window within 1–100 percent of the observed underlying price
        (default20), across puts/calls. Show coverage, invalid/missing/zero quotes,
        spread medians, volume/OI and four nearest sample contracts per expiry/right.
        Dollar spread and spread/midpoint fraction use valid positive bid/noncrossed
        ask only. Source/trade/fetch times are distinct; bid/ask freshness and sizes
        can be unknown. Partial sampling cannot establish whole-market liquidity or
        quote executability; missing evidence is unverified, not an illiquidity verdict.
        """
        if (
            type(strike_distance_pct) not in (int, float)
            or not math.isfinite(strike_distance_pct)
            or not 1 <= strike_distance_pct <= 100
        ):
            return ToolResult(
                status="invalid_data", reason="Invalid strike coverage window"
            ).model_dump(mode="json")
        request = {
            "operation": "options_liquidity",
            "symbol": symbol,
            "expiries": expiries,
            "as_of_date": clock().date().isoformat(),
        }
        return await invoke(
            request, lambda raw: summarize_options(raw, strike_distance_pct=strike_distance_pct)
        )

    return {
        "public_search": public_search,
        "market_statistics": market_statistics,
        "options_liquidity": options_liquidity,
    }
