"""Standalone bounded public reads, sent to the already configured market Python.

Only named publisher series and one explicitly supplied Yahoo underlying are read.
No scanner, model, source-page extraction, account state, or remote file is used.
"""

import contextlib
import io
import json
import signal
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any

REQUEST: dict[str, Any]
TIMEOUT: float
fetch_publisher_series: Any


def now() -> str:
    return datetime.now(UTC).isoformat()


def collect(request: dict[str, Any]) -> dict[str, Any]:
    import yfinance as yf  # type: ignore[import-not-found]

    result: dict[str, Any] = {"operation": request["operation"], "started_at": now()}
    if request["operation"] == "market_statistics":

        aliases = {"SP500": "^GSPC", "NASDAQCOM": "^IXIC", "VIXCLS": "^VIX"}

        def series(series_id: str) -> dict[str, Any]:
            if series_id not in aliases:
                published: dict[str, Any] = globals()["fetch_publisher_series"](
                    series_id, request["start_date"], request["end_date"]
                )
                return published
            symbol = aliases[series_id]
            result_row: dict[str, Any] = {
                "series_id": series_id,
                "status": "unavailable",
                "source_url": "https://finance.yahoo.com/quote/" + symbol + "/history/",
            }
            try:
                frame = yf.Ticker(symbol).history(
                    start=request["start_date"],
                    end=(date.fromisoformat(request["end_date"]) + timedelta(days=1)).isoformat(),
                    auto_adjust=False,
                    actions=False,
                    raise_errors=True,
                )
                if len(frame) > 4000:
                    raise ValueError("History bound")
                values = json.loads(frame[["Close"]].to_json(orient="split"))["data"]
                result_row.update(
                    {
                        "status": "success",
                        "observations": [
                            {"date": index.date().isoformat(), "value": row[0]}
                            for index, row in zip(frame.index, values, strict=True)
                        ],
                        "bar_timezone": str(frame.index.tz) if frame.index.tz else None,
                    }
                )
            except Exception:
                pass
            result_row["fetched_at"] = now()
            return result_row

        with tempfile.TemporaryDirectory(prefix="explorer-yahoo-index-") as cache:
            yf.set_tz_cache_location(cache)
            with ThreadPoolExecutor(max_workers=4) as executor:
                result["series"] = list(executor.map(series, request["series"]))
    elif request["operation"] == "options_liquidity":

        symbol = request["symbol"]
        result.update({"symbol": symbol, "chains": [], "available_expiries": []})
        with tempfile.TemporaryDirectory(prefix="explorer-yahoo-") as cache:
            yf.set_tz_cache_location(cache)
            ticker = yf.Ticker(symbol)
            available = sorted({date.fromisoformat(e).isoformat() for e in ticker.options})
            selected = (
                request.get("expiries") or [e for e in available if e >= request["as_of_date"]][:3]
            )
            result.update({"available_expiries": available, "selected_expiries": selected})
            fields = [
                "contractSymbol",
                "strike",
                "bid",
                "ask",
                "bidSize",
                "askSize",
                "volume",
                "openInterest",
                "lastTradeDate",
                "quoteTime",
                "currency",
            ]
            for expiry in selected:
                chain_result: dict[str, Any] = {"expiry": expiry, "status": "unavailable"}
                if expiry in available:
                    try:
                        chain = ticker.option_chain(expiry)
                        underlying = chain.underlying or {}
                        chain_result["underlying"] = {
                            k: underlying.get(k)
                            for k in (
                                "symbol",
                                "currency",
                                "regularMarketPrice",
                                "regularMarketTime",
                            )
                        }
                        for right, frame in (("calls", chain.calls), ("puts", chain.puts)):
                            if frame is None or len(frame) > 4000:
                                raise ValueError("missing or unbounded source chain")
                            keep = [field for field in fields if field in frame.columns]
                            chain_result[right] = json.loads(
                                frame[keep].to_json(orient="records", date_format="iso")
                            )
                        chain_result["status"] = "success"
                        result.setdefault("underlying", chain_result["underlying"])
                    except Exception:
                        chain_result = {"expiry": expiry, "status": "unavailable"}
                chain_result["fetched_at"] = now()
                result["chains"].append(chain_result)
    else:
        raise ValueError("unsupported operation")
    result["fetched_at"] = now()
    return result


if __name__ == "__main__":
    envelope: dict[str, Any] = (
        {"request": globals()["REQUEST"], "timeout_seconds": globals()["TIMEOUT"]}
        if "REQUEST" in globals()
        else json.loads(sys.stdin.buffer.read(65537))
    )
    if hasattr(signal, "setitimer"):
        signal.setitimer(signal.ITIMER_REAL, envelope["timeout_seconds"] + 1)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            output = collect(envelope["request"])
    except Exception:
        output = {
            "operation": envelope["request"]["operation"],
            "status": "unavailable",
            "fetched_at": now(),
        }
    sys.stdout.write(json.dumps(output, allow_nan=False))
