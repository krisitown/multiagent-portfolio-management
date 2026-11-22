"""The same synthetic contract runs against offline and fake HTTP adapters."""

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from portfolio_management.contracts.evidence import MarketQuery, MarketResult

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "market-provider"
BACKENDS = ("fixture", "http")


def api(module):
    try:
        return importlib.import_module(f"portfolio_management.adapters.market.{module}")
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("portfolio_management.adapters.market"):
            pytest.fail(f"missing Task10 implementation: {module}")
        raise


def dataset():
    request_file = json.loads((EXAMPLES / "requests.json").read_text())
    response_file = json.loads((EXAMPLES / "responses.json").read_text())
    assert request_file["synthetic"] is response_file["synthetic"] is True
    return request_file, response_file


def query(name, **updates):
    requests, _ = dataset()
    return MarketQuery.model_validate({**requests["requests"][name], **updates})


def provider(backend, *, capabilities=None):
    requests, responses = dataset()
    if backend == "fixture":
        selected = api("fixture").FixtureMarketProvider(EXAMPLES)
        if capabilities is not None:
            selected = api("fixture").FixtureMarketProvider(EXAMPLES, capabilities=capabilities)
        return selected

    async def serve(request):
        incoming = json.loads(request.content)
        identity = {key: value for key, value in incoming.items() if key != "query_id"}
        for name, original in requests["requests"].items():
            if {key: value for key, value in original.items() if key != "query_id"} == identity:
                return httpx.Response(
                    200,
                    json={**responses["responses"][name], "query_id": incoming["query_id"]},
                )
        return httpx.Response(404)

    return api("http").HttpMarketProvider(
        endpoint="https://synthetic-bridge.invalid/v1",
        capabilities=capabilities if capabilities is not None else requests["capabilities"],
        transport=httpx.MockTransport(serve),
        max_retries=0,
    )


@pytest.mark.parametrize("backend", BACKENDS)
async def test_every_supported_query_kind_has_sourced_synthetic_evidence(backend):
    selected = provider(backend)
    assert set(selected.capabilities()) == {
        "universe",
        "fundamentals",
        "events",
        "underlying_quotes",
        "option_chains",
        "source_documents",
    }
    for name in (
        "universe-first",
        "fundamentals-partial",
        "events",
        "quote-delayed",
        "option-chain",
        "source-document",
    ):
        result = await selected.query(query(name))
        assert isinstance(result, MarketResult)
        assert result.status == "success"
        assert result.query_id == name
        assert result.evidence
        assert all(record.content["synthetic"] is True for record in result.evidence)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_page_cursor_and_coverage_remain_explicit_until_final_page(backend):
    selected = provider(backend)
    first = await selected.query(query("universe-first"))
    assert first.complete is False
    assert first.next_cursor == "synthetic-page-2"
    assert first.evidence[0].coverage == ("SYN-A", "SYN-B")
    second = await selected.query(query("universe-second", cursor=first.next_cursor))
    assert second.complete is True
    assert second.next_cursor is None
    assert second.evidence[0].coverage == ("SYN-C",)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_empty_success_is_not_unavailable_or_provider_failure(backend):
    selected = provider(backend)
    empty = await selected.query(query("universe-empty"))
    unavailable = await selected.query(
        MarketQuery(query_id="unsupported", capability="intraday_ticks")
    )
    assert empty.status == "success"
    assert empty.complete is True
    assert empty.evidence == ()
    assert unavailable.status == "unavailable"
    assert unavailable.complete is False
    assert unavailable.evidence == ()
    assert unavailable.reason


@pytest.mark.parametrize("backend", BACKENDS)
async def test_capability_gap_is_visible_and_never_fabricated(backend):
    selected = provider(backend, capabilities=("universe",))
    result = await selected.query(query("quote-delayed"))
    assert selected.capabilities() == ["universe"]
    assert result.status == "unavailable"
    assert result.query_id == "quote-delayed"
    assert result.evidence == ()
    assert result.reason


@pytest.mark.parametrize("backend", BACKENDS)
async def test_partial_fundamentals_keep_unknown_quantity_and_coverage(backend):
    result = await provider(backend).query(query("fundamentals-partial"))
    assert result.status == "success"
    assert result.complete is False
    evidence = result.evidence[0]
    assert evidence.content["revenue"] is None
    assert evidence.content["revenue_availability"] == "unavailable"
    assert evidence.content["debt"] == "12.50"
    assert evidence.units == {"debt": "USD"}
    assert evidence.coverage == ("debt",)
    assert any(finding.code == "coverage_partial" for finding in result.findings)
    assert evidence.findings[0].affected_ids == ("SYN-A",)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_naive_source_time_stays_unresolved_without_timezone_invention(backend):
    result = await provider(backend).query(query("quote-naive"))
    evidence = result.evidence[0]
    assert evidence.source_timestamp is None
    assert evidence.source_time_raw == "2026-10-29 11:00:00"
    assert evidence.source_time_availability == "unknown"
    assert evidence.timezone_state == "unknown"
    assert evidence.source_timezone is None
    assert evidence.delay_state == "unknown"
    assert evidence.fetched_at == datetime(2026, 10, 29, 15, 5, tzinfo=UTC)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_delayed_quote_preserves_observation_fetch_and_provenance(backend):
    result = await provider(backend).query(query("quote-delayed"))
    evidence = result.evidence[0]
    assert evidence.content["bid"] == "20.10"
    assert evidence.content["ask"] == "20.30"
    assert evidence.delay_state == "delayed"
    assert evidence.delay_seconds == 900
    assert evidence.source_timestamp == datetime(2026, 10, 29, 15, tzinfo=UTC)
    assert evidence.fetched_at == datetime(2026, 10, 29, 15, 5, tzinfo=UTC)
    assert evidence.provenance == ("synthetic-quotes", "synthetic-dataset-v1")
    assert evidence.source_url == "https://synthetic-quotes.invalid/synthetic"


@pytest.mark.parametrize("backend", BACKENDS)
async def test_contradictory_sources_survive_without_averaging_or_false_completion(backend):
    result = await provider(backend).query(query("quote-contradictory"))
    assert result.complete is False
    assert [record.content["bid"] for record in result.evidence] == ["21.00", "23.00"]
    assert [record.source for record in result.evidence] == [
        "synthetic-source-a",
        "synthetic-source-b",
    ]
    assert result.findings[0].code == "sources_contradict"
    assert result.findings[0].evidence_refs == ("quote-source-a", "quote-source-b")


async def test_missing_fixture_never_falls_through_to_live_source():
    selected = provider("fixture")
    result = await selected.query(query("quote-delayed", instrument_ids=("SYN-MISSING",)))
    assert result.status == "unavailable"
    assert result.evidence == ()
    assert result.complete is False
    assert result.reason


async def test_fixture_result_is_detached_between_consumers():
    selected = provider("fixture")
    first = await selected.query(query("quote-delayed", query_id="consumer-a"))
    first.evidence[0].content["bid"] = "0"
    second = await selected.query(query("quote-delayed", query_id="consumer-b"))
    assert first.query_id == "consumer-a"
    assert second.query_id == "consumer-b"
    assert second.evidence[0].content["bid"] == "20.10"


@pytest.mark.parametrize("backend", BACKENDS)
async def test_nonfinite_query_parameter_is_explicit_invalid_data(backend):
    result = await provider(backend).query(
        query(
            "quote-delayed",
            parameters={"freshness_seconds": float("nan")},
        )
    )
    assert result.status == "invalid_data"
    assert result.evidence == ()
    assert result.complete is False
