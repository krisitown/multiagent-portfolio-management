"""Small explorer evidence adapter using the existing configured market runtime."""

import asyncio
import json
import math
import re
import shlex
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any

from wheel_team.adapters.market.spark import SSHResearchConfig
from wheel_team.adapters.research import ResearchError, Transport, subprocess_transport

# Metadata describes the source series, not a model-created economic verdict.
# Observation dates are periods/session dates, never publication or release instants.
from .publishers import PUBLISHER_CATALOG

SERIES_CATALOG: dict[str, dict[str, str]] = {
    "SP500": {
        "name": "S&P 500 index",
        "units": "index",
        "frequency": "daily",
        "adjustment": "unadjusted_price_index",
        "publisher": "Yahoo Finance",
        "source_series_id": "^GSPC",
        "source_url": "https://finance.yahoo.com/quote/%5EGSPC/history/",
    },
    "NASDAQCOM": {
        "name": "Nasdaq Composite index",
        "units": "index_1971-02-05=100",
        "frequency": "daily",
        "adjustment": "unadjusted_price_index",
        "publisher": "Yahoo Finance",
        "source_series_id": "^IXIC",
        "source_url": "https://finance.yahoo.com/quote/%5EIXIC/history/",
    },
    "VIXCLS": {
        "name": "Cboe VIX index close",
        "units": "index",
        "frequency": "daily",
        "adjustment": "not_seasonally_adjusted",
        "publisher": "Yahoo Finance",
        "source_series_id": "^VIX",
        "source_url": "https://finance.yahoo.com/quote/%5EVIX/history/",
    },
    **PUBLISHER_CATALOG,
}


def number(value: Any) -> float | int | None:
    return value if type(value) in (int, float) and math.isfinite(value) else None


def nonnegative(row: dict[str, Any], field: str) -> float | int | None:
    value = number(row.get(field))
    return value if value is not None and value >= 0 else None


def aware_time(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError("Missing retrieval time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Retrieval time must be timezone aware")
    return parsed.isoformat()


def validate_request(request: dict[str, Any]) -> None:
    if request.get("operation") == "market_statistics":
        ids = request.get("series")
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 8
            or any(not isinstance(i, str) or i not in SERIES_CATALOG for i in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("Select 1–8 distinct catalog series")
        start, end = (
            date.fromisoformat(request["start_date"]),
            date.fromisoformat(request["end_date"]),
        )
        if not 1 <= (end - start).days <= 3650:
            raise ValueError("Select a 1–3650 day window")
    elif request.get("operation") == "options_liquidity":
        symbol = request.get("symbol")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,11}", symbol):
            raise ValueError("Expected one uppercase underlying symbol")
        date.fromisoformat(request["as_of_date"])
        expiries = request.get("expiries")
        if expiries is not None:
            if (
                not isinstance(expiries, list)
                or not 1 <= len(expiries) <= 3
                or len(set(expiries)) != len(expiries)
            ):
                raise ValueError("Select 1–3 distinct expiries")
            for expiry in expiries:
                if not isinstance(expiry, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry):
                    raise ValueError("Expiry must be YYYY-MM-DD")
                date.fromisoformat(expiry)
    else:
        raise ValueError("Unsupported explorer operation")


class ExplorerBackend:
    """Reuse operator-selected Python/SSH identity; do not add infrastructure/config."""

    def __init__(self, config: SSHResearchConfig, *, transport: Transport | None = None):
        self.config = config
        self.timeout_seconds = config.timeout_seconds
        self._transport = transport or subprocess_transport
        self._source = (
            Path(__file__).with_name("publishers.py").read_text(encoding="utf-8")
            + "\n"
            + Path(__file__).with_name("collector.py").read_text(encoding="utf-8")
        )

    def describe(self) -> dict[str, Any]:
        return {
            "provider": "explorer_public_data",
            "sources": ["US economic publishers", "Yahoo Finance"],
            "capabilities": ["market_statistics", "options_liquidity"],
            "market_wide": False,
            "historical_IV_rank": False,
            "options_coverage": "at most three explicitly reported sampled expiries",
        }

    async def request(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            validate_request(request)
        except (ValueError, TypeError, KeyError):
            raise ResearchError("invalid_data", "Invalid bounded explorer request") from None
        # Source is constant implementation; every caller-controlled value stays on stdin.
        bootstrap = (
            "import json,sys;p=json.load(sys.stdin);"
            "exec(compile(p['source'],'<explorer-helper>','exec'),"
            "{'__name__':'__main__','REQUEST':p['request'],'TIMEOUT':p['timeout_seconds']})"
        )
        body = json.dumps(
            {"source": self._source, "request": request, "timeout_seconds": self.timeout_seconds},
            allow_nan=False,
        ).encode()
        interpreter = self.config.market_python
        argv: tuple[str, ...]
        if self.config.transport == "local":
            argv = (interpreter, "-c", bootstrap)
        else:
            assert self.config.identity_file is not None
            argv = (
                "ssh",
                "-T",
                "-i",
                str(Path(self.config.identity_file).expanduser()),
                "-o",
                "HostKeyAlias=" + str(self.config.host_key_alias),
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=10",
                f"{self.config.user}@{self.config.host}",
                shlex.join((interpreter, "-c", bootstrap)),
            )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                raw = await self._transport(argv, body, None, 2_000_000)
            if len(raw) > 2_000_000:
                raise ValueError("Response exceeds bound")
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get("operation") != request["operation"]:
                raise ValueError("Operation mismatch")
            if (
                request["operation"] == "options_liquidity"
                and result.get("status") != "unavailable"
            ):
                if result.get("symbol") != request["symbol"]:
                    raise ValueError("Underlying identity mismatch")
                if (
                    request.get("expiries") is not None
                    and result.get("selected_expiries") != request["expiries"]
                ):
                    raise ValueError("Requested expiry identity mismatch")
                available = result.get("available_expiries")
                if (
                    not isinstance(available, list)
                    or len(available) > 200
                    or any(
                        not isinstance(e, str) or date.fromisoformat(e).isoformat() != e
                        for e in available
                    )
                    or available != sorted(set(available))
                ):
                    raise ValueError("Invalid available expiry metadata")
                if request.get("expiries") is None:
                    expected = [e for e in available if e >= request["as_of_date"]][:3]
                    if result.get("selected_expiries") != expected:
                        raise ValueError("Default expiry coverage mismatch")
            aware_time(result.get("fetched_at"))
            return result
        except TimeoutError:
            raise ResearchError("retryable_error", "Explorer source request timed out") from None
        except ResearchError:
            raise
        except (ValueError, UnicodeError, TypeError, KeyError):
            raise ResearchError("invalid_data", "Invalid explorer source response") from None
        except Exception:
            raise ResearchError("permanent_error", "Explorer source request failed") from None


def summarize_series(
    series_id: str,
    source_observations: list[dict[str, Any]],
    *,
    start_date: str,
    end_date: str,
    max_observations: int,
    fetched_at: datetime,
) -> dict[str, Any]:
    if not isinstance(source_observations, list) or len(source_observations) > 4000:
        raise ValueError("Invalid observation bound")
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    observations: list[dict[str, Any]] = []
    missing, seen = 0, set()
    for row in source_observations:
        if not isinstance(row, dict):
            raise ValueError("Invalid observation")
        observed = date.fromisoformat(row["date"])
        if observed in seen:
            raise ValueError("Duplicate observation")
        seen.add(observed)
        if not start <= observed <= end:
            continue
        if row["value"] is None:
            missing += 1
            continue
        value = number(row["value"])
        if value is None:
            raise ValueError("Invalid numeric observation")
        observations.append({"date": observed.isoformat(), "value": value})
    observations.sort(key=lambda item: item["date"])
    latest = observations[-1] if observations else None
    first = observations[0] if observations else None
    unit = SERIES_CATALOG[series_id]["units"]
    change = None
    if first and latest:
        delta = latest["value"] - first["value"]
        change = {
            "from": first,
            "to": latest,
            "absolute": delta,
            "unit": "percentage_points" if unit == "percent" else unit,
            "relative_fraction": (latest["value"] / first["value"] - 1)
            if unit != "percent" and first["value"] != 0
            else None,
        }
    return {
        "series_id": series_id,
        **SERIES_CATALOG[series_id],
        "status": "success" if observations else "unavailable",
        "latest": latest,
        "observations": observations[-max_observations:],
        "change": change,
        "fetched_at": fetched_at.isoformat(),
        "release_time": None,
        "latest_age_calendar_days": (end - date.fromisoformat(latest["date"])).days
        if latest
        else None,
        "source_url": SERIES_CATALOG[series_id]["source_url"],
        "coverage": {
            "requested_start": start_date,
            "requested_end": end_date,
            "available_observations": len(observations),
            "missing_values": missing,
            "omitted_observations": max(0, len(observations) - max_observations),
        },
        "limitations": [
            "Latest available downloaded vintage; revisions/release times unavailable.",
            "Observation dates are periods/session dates, not publication instants.",
            "Not intraday, a market-wide stock screen, or historical option IV rank.",
        ],
    }


def summarize_options(raw: dict[str, Any], *, strike_distance_pct: float) -> dict[str, Any]:
    symbol = raw["symbol"]
    chains, selected, available = raw["chains"], raw["selected_expiries"], raw["available_expiries"]
    if (
        not isinstance(chains, list)
        or len(chains) > 3
        or any(not isinstance(chain, dict) for chain in chains)
        or len(selected) > 3
        or len(set(selected)) != len(selected)
        or len(chains) != len(selected)
        or [c.get("expiry") for c in chains] != selected
        or len(available) > 200
    ):
        raise ValueError("Invalid expiry coverage")
    profiles, failed, empty, outside, bad_identity, ambiguous = [], [], [], 0, 0, 0
    underlying = raw.get("underlying") or {}
    for chain in chains:
        expiry = chain["expiry"]
        date.fromisoformat(expiry)
        if chain.get("status") != "success":
            failed.append(expiry)
            continue
        quote = chain.get("underlying") or underlying
        if not isinstance(quote, dict) or quote.get("symbol") not in {None, symbol}:
            raise ValueError("Underlying quote identity mismatch")
        spot = number(quote.get("regularMarketPrice"))
        if spot is None or spot <= 0:
            failed.append(expiry)
            continue
        if chain.get("calls") == [] and chain.get("puts") == []:
            empty.append(expiry)
        for right, key in (("put", "puts"), ("call", "calls")):
            rows = chain[key]
            if not isinstance(rows, list) or len(rows) > 4000:
                raise ValueError("Unbounded chain")
            if any(not isinstance(row, dict) for row in rows):
                raise ValueError("Invalid contract row")
            occurrences = Counter(row.get("contractSymbol") for row in rows)
            kept: list[dict[str, Any]] = []
            counts: dict[str, int] = {}
            for row in rows:
                strike = number(row.get("strike"))
                identity = row.get("contractSymbol")
                if occurrences[identity] > 1:
                    ambiguous += 1
                    continue
                expected = symbol + date.fromisoformat(expiry).strftime("%y%m%d")
                expected += "P" if right == "put" else "C"
                expected += f"{round(strike * 1000):08d}" if strike is not None else ""
                if strike is None or strike <= 0 or identity != expected:
                    bad_identity += 1
                    continue
                if abs(strike / spot - 1) > strike_distance_pct / 100 + 1e-12:
                    outside += 1
                    continue
                bid, ask = number(row.get("bid")), number(row.get("ask"))
                state = (
                    "missing"
                    if bid is None or ask is None
                    else "negative"
                    if min(bid, ask) < 0
                    else "crossed"
                    if ask < bid
                    else "zero_bid"
                    if bid == 0
                    else "two_sided"
                )
                valid = state == "two_sided"
                spread, relative_spread = None, None
                if valid:
                    assert ask is not None and bid is not None
                    spread = ask - bid
                    relative_spread = spread / ((ask + bid) / 2)
                counts[state] = counts.get(state, 0) + 1

                kept.append(
                    {
                        "contract_symbol": identity,
                        "strike": strike,
                        "bid": bid,
                        "ask": ask,
                        "quote_state": state,
                        "spread": spread,
                        "relative_spread_fraction": relative_spread,
                        "bid_size": nonnegative(row, "bidSize"),
                        "ask_size": nonnegative(row, "askSize"),
                        "volume": nonnegative(row, "volume"),
                        "open_interest": nonnegative(row, "openInterest"),
                        "currency": row.get("currency"),
                        "quote_time_raw": row.get("quoteTime"),
                        "last_trade_time_raw": row.get("lastTradeDate"),
                    }
                )
            spreads = [r["spread"] for r in kept if r["spread"] is not None]
            currencies = {r["currency"] for r in kept if isinstance(r["currency"], str)}
            relative = [r["relative_spread_fraction"] for r in kept if r["spread"] is not None]
            profile = {
                "expiry": expiry,
                "right": right,
                "underlying": quote,
                "fetched_at": aware_time(chain.get("fetched_at", raw["fetched_at"])),
                "contracts_source_count": len(rows),
                "contracts_in_window": len(kept),
                "currency": next(iter(currencies)) if len(currencies) == 1 else None,
                "quote_states": counts,
                "mixed_currencies": len(currencies) > 1,
                "spread_median": median(spreads) if spreads and len(currencies) <= 1 else None,
                "relative_spread_median": median(relative) if relative else None,
                "sample_contracts": sorted(
                    kept, key=lambda r: (abs(r["strike"] / spot - 1), r["strike"])
                )[:4],
                "sample_selection": "four nearest strikes, including invalid/missing quotes",
                "sample_omitted_contracts": max(0, len(kept) - 4),
            }
            for field in ("volume", "open_interest"):
                values = [r[field] for r in kept if r[field] is not None]
                profile[field + "_total_observed"] = sum(values) if values else None
                profile[field + "_missing"] = len(kept) - len(values)
            profiles.append(profile)
    return {
        "symbol": symbol,
        "source": "Yahoo Finance option chains",
        "source_url": f"https://finance.yahoo.com/quote/{symbol}/options/",
        "fetched_at": raw["fetched_at"],
        "underlying": underlying,
        "profiles": profiles,
        "liquidity_verdict": "not_assessed",
        "formulas": {
            "spread": "ask - bid, positive bid and noncrossed ask only",
            "relative_spread_fraction": "(ask-bid)/((ask+bid)/2)",
        },
        "units": {
            "bid_ask_spread": "source_contract_currency_per_profile",
            "relative_spread_fraction": "fraction_of_midpoint",
            "volume_open_interest": "contracts",
        },
        "coverage": {
            "market_wide": False,
            "available_expiries": available[:80],
            "available_expiries_omitted": max(0, len(available) - 80),
            "selected_expiries": selected,
            "failed_expiries": failed,
            "empty_expiries": empty,
            "partial_coverage": bool(failed or empty),
            "strike_distance_pct": strike_distance_pct,
            "contracts_outside_window": outside,
            "invalid_identity_rows": bad_identity,
            "ambiguous_identity_rows": ambiguous,
            "quote_freshness": "unknown; no inferred bid/ask timestamp",
        },
        "limitations": [
            "Sampled expiries and symmetric strike window; not the whole options market.",
            "No automatic liquidity score, liquidity clearance or execution approval.",
            "Last-trade time and retrieval time are not bid/ask quote times.",
            "Volume/OI dates and quote persistence are unavailable; sizes may be missing.",
            "Standard source symbols only; adjusted deliverables unverified.",
        ],
    }
