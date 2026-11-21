"""A response-only fundamentals view; persisted source records remain complete."""

from copy import deepcopy
from typing import Any

_SELECTED_FIELDS = frozenset(
    {
        "symbol",
        "shortName",
        "longName",
        "sector",
        "industry",
        "currency",
        "financialCurrency",
        "regularMarketPrice",
        "regularMarketTime",
        "marketCap",
        "enterpriseValue",
        "trailingPE",
        "forwardPE",
        "enterpriseToEbitda",
        "totalDebt",
        "totalCash",
        "debtToEquity",
        "freeCashflow",
        "operatingCashflow",
        "totalRevenue",
        "revenueGrowth",
        "earningsGrowth",
        "profitMargins",
        "operatingMargins",
        "returnOnAssets",
        "returnOnEquity",
        "dividendRate",
        "dividendYield",
        "payoutRatio",
        "lastDividendValue",
        "exDividendDate",
        "dividendDate",
        "sharesOutstanding",
        "longBusinessSummary",
        "lastFiscalYearEnd",
        "nextFiscalYearEnd",
        "mostRecentQuarter",
        "lastDividendDate",
    }
)


def fundamentals_view(record: dict[str, Any], record_ref: str) -> dict[str, Any]:
    """Select known fundamentals fields without changing source values or metadata."""
    content = record.get("content")
    if (
        record.get("source") != "yahoo-fundamentals"
        or not isinstance(content, dict)
        or content.get("capability") != "fundamentals"
        or not isinstance(content.get("data"), dict)
        or "tool_response_projection" in content
    ):
        return record
    data = content["data"]
    if "financialCurrency" not in data or not any(
        field in data and (data[field] is None or type(data[field]) in (str, int, float))
        for field in ("marketCap", "totalRevenue", "totalDebt", "trailingPE", "freeCashflow")
    ):
        return record
    selected = sorted(field for field in data if field in _SELECTED_FIELDS)
    if not selected:
        return record
    omitted = sorted(field for field in data if field not in _SELECTED_FIELDS)
    projected = deepcopy(record)
    projected["content"]["data"] = {
        field: projected["content"]["data"][field] for field in selected
    }
    projected["content"]["tool_response_projection"] = {
        "view": "fundamentals_v1",
        "selected_data_fields": selected,
        "original_data_field_count": len(data),
        "omitted_data_field_count": len(omitted),
        "omitted_data_fields": [field[:128] for field in omitted[:32]],
        "omitted_data_fields_truncated": len(omitted) > 32
        or any(len(field) > 128 for field in omitted),
        "hash_semantics": "original_record_and_source_not_projection",
        "stored_record_unchanged": True,
        "original_record_ref": record_ref,
        "original_access": "fact_check_and_audit",
    }
    return projected
