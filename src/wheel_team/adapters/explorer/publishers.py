"""Bounded direct-publisher macro reads; standalone for the configured market runtime.

No package imports or import-time I/O. Dates identify observation periods, not
publication instants. BLS requests use its unregistered v1 public API.
"""

import hashlib
import json
import math
import time
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from importlib import import_module
from typing import Any
from urllib.parse import urlencode

PUBLISHER_CATALOG: dict[str, dict[str, str]] = {
    "UNRATE": {
        "name": "Civilian unemployment rate",
        "units": "percent",
        "frequency": "monthly",
        "adjustment": "seasonally_adjusted",
        "publisher": "U.S. Bureau of Labor Statistics",
        "source_url": "https://www.bls.gov/cps/",
        "source_series_id": "LNS14000000",
    },
    "CPIAUCSL": {
        "name": "CPI all urban consumers, all items",
        "units": "index_1982-1984=100",
        "frequency": "monthly",
        "adjustment": "seasonally_adjusted",
        "publisher": "U.S. Bureau of Labor Statistics",
        "source_url": "https://www.bls.gov/cpi/",
        "source_series_id": "CUSR0000SA0",
    },
    "PAYEMS": {
        "name": "All employees, total nonfarm",
        "units": "thousands_of_persons",
        "frequency": "monthly",
        "adjustment": "seasonally_adjusted",
        "publisher": "U.S. Bureau of Labor Statistics",
        "source_url": "https://www.bls.gov/ces/",
        "source_series_id": "CES0000000001",
    },
    "DFF": {
        "name": "Effective federal funds rate, published business days",
        "units": "percent",
        "frequency": "daily",
        "adjustment": "not_seasonally_adjusted",
        "publisher": "Federal Reserve Bank of New York",
        "source_url": "https://www.newyorkfed.org/markets/reference-rates/effr",
        "source_series_id": "EFFR",
    },
    "DGS2": {
        "name": "2-year Treasury par yield curve rate",
        "units": "percent",
        "frequency": "daily",
        "adjustment": "not_seasonally_adjusted",
        "publisher": "U.S. Treasury",
        "source_url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates",
        "source_series_id": "BC_2YEAR",
    },
    "DGS10": {
        "name": "10-year Treasury par yield curve rate",
        "units": "percent",
        "frequency": "daily",
        "adjustment": "not_seasonally_adjusted",
        "publisher": "U.S. Treasury",
        "source_url": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates",
        "source_series_id": "BC_10YEAR",
    },
}


def _publisher_request(
    url: str, *, json_body: dict[str, Any] | None = None, timeout: float = 15
) -> bytes:
    """One fixed-publisher HTTP request, bounded before parsing and log forwarding."""
    requests = import_module("curl_cffi").requests

    kwargs: dict[str, Any] = {"impersonate": "chrome", "timeout": timeout, "stream": True}
    if json_body is None:
        response = requests.get(url, **kwargs)
    else:
        response = requests.post(url, json=json_body, **kwargs)
    try:
        response.raise_for_status()
        chunks: list[bytes] = []
        size = 0
        for block in response.iter_content(chunk_size=65536):
            size += len(block)
            if size > 1_000_000:
                raise ValueError("Publisher response exceeds bound")
            chunks.append(block)
        return b"".join(chunks)
    finally:
        response.close()


def _publisher_value(value: Any) -> float | None:
    if value is None or value in ("", "-", ".", "N/A"):
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("Invalid observation value")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("Nonfinite observation value")
    return parsed


def _publisher_bls(raw: bytes, source_id: str) -> list[dict[str, Any]]:
    payload = json.loads(raw)
    if payload.get("status") != "REQUEST_SUCCEEDED" or payload.get("message"):
        raise ValueError("BLS request unavailable or limited")
    series = payload["Results"]["series"]
    if not isinstance(series, list) or len(series) != 1 or series[0]["seriesID"] != source_id:
        raise ValueError("BLS series identity mismatch")
    rows = series[0]["data"]
    if not isinstance(rows, list) or len(rows) > 130:
        raise ValueError("BLS observation bound")
    result = []
    for row in rows:
        period = row["period"]
        if period == "M13":  # Annual average is not a monthly observation.
            continue
        if not isinstance(period, str) or period not in {f"M{i:02d}" for i in range(1, 13)}:
            raise ValueError("Unexpected BLS period")
        observed = date(int(row["year"]), int(period[1:]), 1)
        result.append({"date": observed.isoformat(), "value": _publisher_value(row["value"])})
    return result


def _publisher_nyfed(raw: bytes) -> list[dict[str, Any]]:
    rows = json.loads(raw)["refRates"]
    if not isinstance(rows, list) or len(rows) > 4000:
        raise ValueError("NY Fed observation bound")
    result = []
    for row in rows:
        if row.get("type") != "EFFR":
            raise ValueError("NY Fed series identity mismatch")
        result.append(
            {
                "date": date.fromisoformat(row["effectiveDate"]).isoformat(),
                "value": _publisher_value(row["percentRate"]),
            }
        )
    return result


def _publisher_treasury(raw: bytes, source_id: str) -> list[dict[str, Any]]:
    root = ET.fromstring(raw)
    if root.tag != "{http://www.w3.org/2005/Atom}feed":
        raise ValueError("Unexpected Treasury document")
    entries = root.findall("{http://www.w3.org/2005/Atom}entry")
    if len(entries) > 370:
        raise ValueError("Treasury annual observation bound")
    result = []
    namespace = "{http://schemas.microsoft.com/ado/2007/08/dataservices}"
    for entry in entries:
        date_node = entry.find(".//" + namespace + "NEW_DATE")
        value_node = entry.find(".//" + namespace + source_id)
        if date_node is None or date_node.text is None or value_node is None:
            raise ValueError("Treasury series identity missing")
        observed = datetime.fromisoformat(date_node.text).date().isoformat()
        result.append({"date": observed, "value": _publisher_value(value_node.text)})
    return result


def fetch_publisher_series(series_id: str, start_date: str, end_date: str) -> dict[str, Any]:
    """Return one catalog series, at most 3650 days, within a 15-second read budget.

    BLS is limited to ten calendar years per request and 25 unregistered queries
    daily. Treasury uses bounded annual files. Rate limits/timeouts are unavailable
    evidence, not zero observations. No data is forward-filled or interpolated.
    """
    fetched_at = datetime.now(UTC).isoformat()
    catalog = PUBLISHER_CATALOG.get(series_id, {})
    result: dict[str, Any] = {
        "series_id": series_id,
        "status": "unavailable",
        "observations": [],
        "fetched_at": fetched_at,
        "source_url": catalog.get("source_url"),
        "metadata": dict(catalog),
    }
    metadata = result["metadata"]
    try:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if not catalog or not 0 <= (end - start).days <= 3650:
            raise ValueError("Invalid publisher request")
        metadata.update({"requested_start": start.isoformat(), "requested_end": end.isoformat()})
        deadline = time.monotonic() + 15
        responses, observations = [], []

        def request(url: str, body: dict[str, Any] | None = None) -> bytes:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Publisher budget exhausted")
            raw = _publisher_request(url, json_body=body, timeout=remaining)
            if len(raw) > 1_000_000:
                raise ValueError("Publisher response exceeds bound")
            responses.append(
                {"source_url": url, "sha256": hashlib.sha256(raw).hexdigest(), "request_json": body}
            )
            return raw

        source_id = catalog["source_series_id"]
        if catalog["publisher"] == "U.S. Bureau of Labor Statistics":
            url = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
            for year in range(start.year, end.year + 1, 10):
                body = {
                    "seriesid": [source_id],
                    "startyear": str(year),
                    "endyear": str(min(year + 9, end.year)),
                }
                observations.extend(_publisher_bls(request(url, body), source_id))
            metadata["unregistered_daily_query_limit"] = 25
        elif source_id == "EFFR":
            url = (
                "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?"
                + urlencode({"startDate": start.isoformat(), "endDate": end.isoformat()})
            )
            observations = _publisher_nyfed(request(url))
            metadata["observation_calendar"] = "Published business-day effective dates only"
        else:
            for year in range(start.year, end.year + 1):
                url = (
                    "https://home.treasury.gov/resource-center/data-chart-center/"
                    "interest-rates/pages/xml?"
                    + urlencode(
                        {"data": "daily_treasury_yield_curve", "field_tdr_date_value": year}
                    )
                )
                observations.extend(_publisher_treasury(request(url), source_id))
        seen = set()
        for row in observations:
            if row["date"] in seen:
                raise ValueError("Duplicate source observation")
            seen.add(row["date"])
        kept = sorted(
            (row for row in observations if start.isoformat() <= row["date"] <= end.isoformat()),
            key=lambda row: row["date"],
        )
        metadata.update(
            {
                "responses": responses,
                "available_start": kept[0]["date"] if kept else None,
                "available_end": kept[-1]["date"] if kept else None,
                "release_time": None,
                "vintage": "Latest downloaded publisher vintage; may be revised",
            }
        )
        result.update(
            {
                "observations": kept,
                "status": "success"
                if any(row["value"] is not None for row in kept)
                else "unavailable",
            }
        )
    except Exception:
        result["observations"] = []
        metadata["reason"] = "Publisher data unavailable, invalid or outside bounded coverage"
    result["fetched_at"] = datetime.now(UTC).isoformat()
    return result
