"""Offline mechanical response views, not issuer facts or investment acceptance."""

import copy
import json

import pytest
from strands import Agent, tool

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_vault import evidence
from wheel_team.contracts import EvidenceRecord, MarketResult
from wheel_team.tools.evidence import evidence_tools
from wheel_team.tools.market import market_tools
from wheel_team.types import ResearchResult
from wheel_team.vault import EvidenceStore


def fundamentals():
    selected = {
        "symbol": "MECHANICAL_TEST",
        "shortName": "Mechanical fixture",
        "longName": "Isolated response-view fixture",
        "sector": "test sector",
        "industry": "test industry",
        "currency": "USD",
        "financialCurrency": "USD",
        "regularMarketPrice": 0,
        "regularMarketTime": None,
        "marketCap": 10,
        "enterpriseValue": 11,
        "trailingPE": None,
        "forwardPE": 0,
        "enterpriseToEbitda": 3,
        "totalDebt": 4,
        "totalCash": 5,
        "debtToEquity": 6,
        "freeCashflow": 7,
        "operatingCashflow": 8,
        "totalRevenue": 9,
        "revenueGrowth": 0,
        "earningsGrowth": None,
        "profitMargins": 0,
        "operatingMargins": 0,
        "returnOnAssets": 0,
        "returnOnEquity": 0,
        "dividendRate": 0,
        "dividendYield": None,
        "payoutRatio": 0,
        "lastDividendValue": 0,
        "exDividendDate": None,
        "dividendDate": 1793404800,
        "sharesOutstanding": 1,
        "longBusinessSummary": "Untrimmed mechanical description. " * 300,
        "lastFiscalYearEnd": 1767139200,
        "nextFiscalYearEnd": 1798675200,
        "mostRecentQuarter": None,
        "lastDividendDate": 0,
    }
    record = EvidenceRecord.model_validate(
        evidence(
            "fundamentals-mechanical",
            source="yahoo-fundamentals",
            source_url="https://example.invalid/mechanical-only",
            content_hash="original-source-hash",
            units={"marketCap": "USD", "totalRevenue": "source-unverified-scale"},
            provenance=["mechanical-source"],
            coverage=["bounded-mechanical-observation"],
            findings=[{"code": "test", "severity": "warning", "reason": "No acceptance"}],
            content={
                "capability": "fundamentals",
                "instrument_id": "MECHANICAL_TEST",
                "capture_id": "mechanical-capture",
                "source": {"provider": "offline"},
                "limits": {"truncated": True, "source_bytes": 100000},
                "data": {
                    **selected,
                    "companyOfficers": [{"name": "mechanical contact"}] * 100,
                    "phone": "mechanical contact only",
                },
            },
        )
    ).model_dump(mode="json")
    return record, selected


class MechanicalProvider:
    def __init__(self, record):
        self.record = record

    def capabilities(self):
        return ["fundamentals"]

    async def query(self, query):
        return MarketResult(
            query_id=query.query_id,
            status="success",
            evidence=(EvidenceRecord.model_validate(self.record),),
            complete=False,
            next_cursor="mechanical-next-page",
            findings=({"code": "page", "severity": "info", "reason": "More pages"},),
        )


def tool_result(call):
    result = call["messages"][-1]["content"][0]["toolResult"]
    return json.loads(result["content"][0]["text"])


async def test_sdk_market_and_default_read_are_compact_full_read_rejected_storage_exact(tmp_path):
    original, selected = fundamentals()
    before = copy.deepcopy(original)
    provider = MechanicalProvider(original)
    store = EvidenceStore(tmp_path / "records")
    ref = "evidence:fundamentals-mechanical"
    model = ScriptModel(
        output("market_query", query={"query_id": "mechanical", "capability": "fundamentals"}),
        output("evidence_read", record_ref=ref),
        output("evidence_read", record_ref=ref, view="full"),
        output("ResearchResult", job_id="mechanical", outcome="success", summary="View inspected"),
    )
    agent = Agent(
        model=model,
        tools=[
            tool(fn) for fn in {**market_tools(store, provider), **evidence_tools(store)}.values()
        ],
        callback_handler=None,
    )
    await agent.invoke_async(
        "Inspect mechanical response views", structured_output_model=ResearchResult
    )
    market, compact = [tool_result(call) for call in model.calls[1:3]]
    rejected = model.calls[3]["messages"][-1]["content"][0]["toolResult"]
    assert "Input should be" in rejected["content"][0]["text"]
    projected = market["data"]["evidence"][0]
    assert projected["content"]["data"] == selected
    assert compact["data"]["record"]["tool_response_projection"]["excerpt_only"] is True
    assert len(json.dumps(compact)) < 20000
    assert compact["result_refs"] == [ref]
    assert market["evidence_refs"] == market["result_refs"] == [ref]
    assert market["status"] == "success"
    assert market["data"]["complete"] is False
    assert market["data"]["next_cursor"] == "mechanical-next-page"
    assert market["data"]["query_id"] == "mechanical"
    assert market["findings"] == market["data"]["findings"]
    assert market["findings"][0]["code"] == "page"
    assert provider.record == store.get(ref) == before
    persisted = next((tmp_path / "records").glob("*.json")).read_bytes()
    assert json.loads(persisted)["record"] == before
    await evidence_tools(store)["evidence_read"](ref)
    assert next((tmp_path / "records").glob("*.json")).read_bytes() == persisted
    spec = next(spec for spec in model.calls[0]["tools"] if spec["name"] == "evidence_read")
    schema = spec["inputSchema"]["json"]
    assert schema["properties"]["view"]["const"] == "compact"
    assert "view" not in schema.get("required", [])
    assert "bounded" in spec["description"]


def test_projection_retains_metadata_and_selected_values_detached_from_original():
    from wheel_team.tools.fundamentals import fundamentals_view

    original, selected = fundamentals()
    before = copy.deepcopy(original)
    ref = "evidence:fundamentals-mechanical"
    projected = fundamentals_view(original, ref)
    assert projected["content"]["data"] == selected
    metadata = projected["content"]["tool_response_projection"]
    assert metadata == {
        "view": "fundamentals_v1",
        "selected_data_fields": sorted(selected),
        "original_data_field_count": 40,
        "omitted_data_field_count": 2,
        "omitted_data_fields": ["companyOfficers", "phone"],
        "omitted_data_fields_truncated": False,
        "hash_semantics": "original_record_and_source_not_projection",
        "stored_record_unchanged": True,
        "original_record_ref": ref,
        "original_access": "fact_check_and_audit",
    }
    assert {key: value for key, value in projected.items() if key != "content"} == {
        key: value for key, value in before.items() if key != "content"
    }
    assert {
        key: value
        for key, value in projected["content"].items()
        if key not in {"data", "tool_response_projection"}
    } == {key: value for key, value in before["content"].items() if key != "data"}
    projected["content"]["source"]["provider"] = "changed"
    projected["units"]["marketCap"] = "changed"
    projected["findings"][0]["reason"] = "changed"
    assert original == before


@pytest.mark.parametrize("count,total,returned", [(1, 4, 1), (40, 43, 32)])
def test_omitted_names_are_bounded_without_hiding_total_or_complete_business_description(
    count, total, returned
):
    from wheel_team.tools.fundamentals import fundamentals_view

    record, selected = fundamentals()
    record["content"]["data"] = {
        "financialCurrency": None,
        "marketCap": None if count == 1 else 0,
        "longBusinessSummary": selected["longBusinessSummary"],
        **{str(i) + "x" * 200: "not selected" for i in range(count)},
    }
    result = fundamentals_view(record, "evidence:fundamentals-mechanical")["content"]
    assert result["data"]["financialCurrency"] is None
    assert result["data"]["marketCap"] == (None if count == 1 else 0)
    assert result["data"]["longBusinessSummary"] == selected["longBusinessSummary"]
    projection = result["tool_response_projection"]
    assert projection["original_data_field_count"] == total
    assert projection["omitted_data_field_count"] == count
    assert len(projection["omitted_data_fields"]) == returned
    assert all(len(name) <= 128 for name in projection["omitted_data_fields"])
    assert projection["omitted_data_fields_truncated"] is True


@pytest.mark.parametrize(
    "content",
    [
        {"capability": "option_chains", "data": {"symbol": "MECHANICAL", "multiplier": None}},
        {"capability": "fundamentals", "data": ["unknown shape"]},
        {"capability": "fundamentals", "data": {"unknownField": "preserve"}},
        {
            "capability": "fundamentals",
            "data": {"symbol": "MECHANICAL", "financialCurrency": "USD", "marketCap": 0},
            "tool_response_projection": {"original": "preserve"},
        },
        "unknown content shape",
    ],
)
async def test_nonfundamentals_and_unknown_or_colliding_shapes_are_returned_unchanged(
    tmp_path, content
):
    from wheel_team.tools.fundamentals import fundamentals_view

    record = EvidenceRecord.model_validate(
        evidence(source="yahoo-fundamentals", content=content)
    ).model_dump(mode="json")
    store = EvidenceStore(tmp_path / "records")
    ref = store.put("evidence", record)
    assert fundamentals_view(record, ref) == record
    assert (await evidence_tools(store)["evidence_read"](ref))["data"]["record"] == record
    assert (await evidence_tools(store)["evidence_read"](ref, view="full"))[
        "status"
    ] == "invalid_data"
    market = await market_tools(store, MechanicalProvider(record))["market_query"](
        {"query_id": "mechanical", "capability": "fundamentals"}
    )
    assert market["data"]["evidence"] == [record]


@pytest.mark.parametrize(
    "source,data",
    [
        (
            "unknown-fundamentals-producer",
            {
                "symbol": "MECHANICAL",
                "financialCurrency": "USD",
                "marketCap": 0,
                "businessFacts": {"revenue": "preserve"},
            },
        ),
        (
            "yahoo-fundamentals",
            {"symbol": "MECHANICAL", "revenue": {"value": "123", "period": "FY2025"}},
        ),
        (
            "yahoo-fundamentals",
            {
                "financialCurrency": "USD",
                "totalRevenue": {"value": "123", "period": "FY2025"},
                "businessFacts": "preserve",
            },
        ),
    ],
)
async def test_unfamiliar_producer_or_financial_shape_preserves_exact_market_and_reads(
    tmp_path, source, data
):
    record = EvidenceRecord.model_validate(
        evidence(source=source, content={"capability": "fundamentals", "data": data})
    ).model_dump(mode="json")
    store = EvidenceStore(tmp_path / "records")
    provider = MechanicalProvider(record)
    market = await market_tools(store, provider)["market_query"](
        {"query_id": "mechanical", "capability": "fundamentals"}
    )
    ref = market["result_refs"][0]
    assert market["data"]["evidence"] == [record]
    api = evidence_tools(store)
    assert (await api["evidence_read"](ref))["data"]["record"] == record
    assert (await api["evidence_read"](ref, view="full"))["status"] == "invalid_data"
    assert provider.record == store.get(ref) == record


@pytest.mark.parametrize("view", ["unknown", None, [], True])
async def test_direct_read_rejects_invalid_view_with_actionable_modes(tmp_path, view):
    store = EvidenceStore(tmp_path / "records")
    ref = store.put("evidence", fundamentals()[0])
    result = await evidence_tools(store)["evidence_read"](ref, view=view)
    assert result["status"] == "invalid_data"
    assert "compact" in result["reason"]
    assert result["data"] is None and result["result_refs"] == []
