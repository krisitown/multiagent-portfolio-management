"""Explorer boundaries: public captures plus explicitly synthetic arithmetic/error cases."""

import asyncio
import importlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from portfolio_management.adapters.market.spark import SSHResearchConfig
from portfolio_management.adapters.research import HermesResearchBackend, ResearchBackendConfig
from portfolio_management.vault import EvidenceStore

NOW = datetime(2026, 10, 31, 21, tzinfo=UTC)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "explorer"


def api():
    try:
        return importlib.import_module(
            "portfolio_management.adapters.explorer"
        ), importlib.import_module("portfolio_management.tools.explorer")
    except ModuleNotFoundError:
        pytest.fail("the agreed explorer tool/provider implementation is missing")


def market_config():
    return SSHResearchConfig(
        market_python="/configured/market/python",
        research_python="/configured/hermes/python",
        scanner_path="/configured/scanner.py",
        hermes_project="/configured/hermes",
        hermes_env="/configured/private/.env",
        capture_root=Path("/configured/captures"),
        transport="local",
        timeout_seconds=5,
    )


class Boundary:
    """Only the external process is replaced; validation/parsing/storage remain real."""

    def __init__(self, response):
        self.response, self.calls = response, []

    async def __call__(self, argv, stdin, cwd, max_bytes):
        self.calls.append((argv, json.loads(stdin), cwd, max_bytes))
        return json.dumps(self.response).encode()


async def test_explorer_surface_excludes_fetch_and_missing_sources_are_unavailable(tmp_path):
    _, tools = api()
    calls = tools.explorer_tools(EvidenceStore(tmp_path), None, None, lambda: NOW)
    assert set(calls) == {"public_search", "market_statistics", "options_liquidity"}
    assert (await calls["public_search"]("Apple earnings"))["status"] == "unavailable"
    assert (await calls["options_liquidity"]("AAPL"))["status"] == "unavailable"
    assert (await calls["market_statistics"](["SP500"]))["status"] == "unavailable"
    catalog = await calls["market_statistics"]([])
    assert catalog["status"] == "success"
    assert catalog["data"]["catalog"]["UNRATE"]["units"] == "percent"
    assert catalog["data"]["catalog"]["PAYEMS"]["frequency"] == "monthly"
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize(
    "name,args",
    [
        ("market_statistics", (["SP500", "SP500"],)),
        ("market_statistics", (["SP500"], 0)),
        ("market_statistics", (["SP500"], 3651)),
        ("market_statistics", (["SP500"], 90, True)),
        ("options_liquidity", ("AAPL;echo secret",)),
        ("options_liquidity", ("AAPL", ["2026-02-30"])),
        ("options_liquidity", ("AAPL", ["2026-11-06"] * 2)),
        ("options_liquidity", ("AAPL", None, 0)),
        ("options_liquidity", ("AAPL", None, 101)),
    ],
)
async def test_invalid_requests_never_reach_public_sources(tmp_path, name, args):
    adapter, tools = api()
    transport = Boundary({})
    backend = adapter.ExplorerBackend(market_config(), transport=transport)
    call = tools.explorer_tools(EvidenceStore(tmp_path), backend, None, lambda: NOW)[name]
    assert (await call(*args))["status"] == "invalid_data"
    assert not transport.calls


async def test_unsupported_economics_returns_available_catalog_without_inventing_data(tmp_path):
    _, tools = api()
    result = await tools.explorer_tools(EvidenceStore(tmp_path), None, None)["market_statistics"](
        ["GDPC1", "BAMLH0A0HYM2"]
    )
    assert result["status"] == "unavailable"
    assert result["data"]["unsupported_series"] == ["GDPC1", "BAMLH0A0HYM2"]
    assert "UNRATE" in result["data"]["catalog"]
    assert "GDPC1" not in result["data"]["catalog"]


async def test_search_exposes_dates_and_bounds_excerpts_without_fetching(tmp_path):
    _, tools = api()
    # Error/truncation boundary fixture; not market acceptance evidence.
    transport = Boundary(
        {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": "Apple reports results",
                        "url": "https://www.apple.com/newsroom/",
                        "description": "a" * 1500,
                        "published_date": "2026-10-29",
                    }
                ]
            },
        }
    )
    research = HermesResearchBackend(
        ResearchBackendConfig(
            python="/configured/python", cwd="/configured/hermes", env_file="/private/env"
        ),
        transport=transport,
    )
    store = EvidenceStore(tmp_path)
    result = await tools.explorer_tools(store, None, research, lambda: NOW)["public_search"](
        "Apple results", 1
    )
    row = result["data"]["results"][0]
    assert len(row["text"]) == 800
    assert row["truncated"] is True
    assert row["source_time_raw"] == "2026-10-29"
    assert row["source_timestamp"] is None
    assert row["fetched_at"] == "2026-10-31T21:00:00Z"
    assert row["source_identity"] == "www.apple.com"
    assert "excerpt" in result["data"]["coverage_note"].lower()
    assert transport.calls[0][1]["operation"] == "search"
    assert len(transport.calls) == 1


def test_series_arithmetic_uses_observation_dates_not_release_or_retrieval_times():
    adapter, _ = api()
    # Synthetic arithmetic fixture, deliberately includes a missing holiday value.
    data = adapter.summarize_series(
        "UNRATE",
        [
            {"date": "2026-10-28", "value": 4.9},
            {"date": "2026-10-29", "value": None},
            {"date": "2026-10-30", "value": 5.0},
        ],
        start_date="2026-10-01",
        end_date="2026-10-31",
        max_observations=1,
        fetched_at=NOW,
    )
    assert data["latest"] == {"date": "2026-10-30", "value": 5.0}
    assert data["change"]["absolute"] == pytest.approx(0.1)
    assert data["change"]["unit"] == "percentage_points"
    assert data["change"]["relative_fraction"] is None
    assert data["coverage"]["missing_values"] == 1
    assert data["coverage"]["omitted_observations"] == 1
    assert data["latest_age_calendar_days"] == 1
    assert data["release_time"] is None
    assert data["observations"] == [{"date": "2026-10-30", "value": 5.0}]


def arithmetic_chain():
    """Hand-built quote arithmetic only; this is not an AAPL acceptance capture."""
    return {
        "operation": "options_liquidity",
        "symbol": "AAPL",
        "fetched_at": NOW.isoformat(),
        "available_expiries": ["2026-11-06"],
        "selected_expiries": ["2026-11-06"],
        "underlying": {
            "regularMarketPrice": 100,
            "regularMarketTime": 1793386800,
            "currency": "USD",
        },
        "chains": [
            {
                "expiry": "2026-11-06",
                "status": "success",
                "calls": [],
                "puts": [
                    {
                        "contractSymbol": "AAPL261106P00100000",
                        "strike": 100,
                        "bid": 2,
                        "ask": 3,
                        "volume": 0,
                        "openInterest": 20,
                        "lastTradeDate": "2026-10-30T19:00:00+00:00",
                    },
                    {"contractSymbol": "AAPL261106P00095000", "strike": 95, "bid": 0, "ask": 1},
                    {"contractSymbol": "AAPL261106P00090000", "strike": 90, "bid": 2, "ask": 1},
                    {"contractSymbol": "AAPL261106P00105000", "strike": 105, "bid": None, "ask": 1},
                    {"contractSymbol": "AAPL261106P00150000", "strike": 150, "bid": 0, "ask": 10},
                ],
            }
        ],
    }


def test_liquidity_arithmetic_keeps_zero_crossed_missing_and_trade_time_distinct():
    adapter, _ = api()
    result = adapter.summarize_options(arithmetic_chain(), strike_distance_pct=20)
    puts = next(r for r in result["profiles"] if r["right"] == "put")
    assert puts["contracts_in_window"] == 4
    assert puts["quote_states"] == {"two_sided": 1, "zero_bid": 1, "crossed": 1, "missing": 1}
    assert puts["spread_median"] == 1
    assert puts["relative_spread_median"] == pytest.approx(0.4)
    assert puts["volume_total_observed"] == 0
    assert puts["volume_missing"] == 3
    assert puts["open_interest_missing"] == 3
    quote = next(r for r in puts["sample_contracts"] if r["strike"] == 100)
    assert quote["spread"] == 1
    assert quote["relative_spread_fraction"] == pytest.approx(0.4)
    assert quote["bid_size"] is None and quote["ask_size"] is None
    assert quote["quote_time_raw"] is None
    assert quote["last_trade_time_raw"] == "2026-10-30T19:00:00+00:00"
    assert result["liquidity_verdict"] == "not_assessed"
    assert result["coverage"]["contracts_outside_window"] == 1
    assert result["coverage"]["market_wide"] is False


async def test_partial_failure_is_visible_and_evidence_persists_units(tmp_path):
    adapter, tools = api()
    response = arithmetic_chain()
    response["chains"].append({"expiry": "2026-11-13", "status": "unavailable"})
    response["selected_expiries"].append("2026-11-13")
    response["available_expiries"].append("2026-11-13")
    transport = Boundary(response)
    backend = adapter.ExplorerBackend(market_config(), transport=transport)
    store = EvidenceStore(tmp_path)
    result = await tools.explorer_tools(store, backend, None, lambda: NOW)["options_liquidity"](
        "AAPL"
    )
    assert result["status"] == "success"
    assert result["data"]["coverage"]["failed_expiries"] == ["2026-11-13"]
    evidence = store.get(result["evidence_refs"][0])
    assert evidence["units"]["relative_spread_fraction"] == "fraction_of_midpoint"
    assert evidence["source_timestamp"] is None
    assert evidence["fetched_at"] == "2026-10-31T21:00:00Z"
    argv, envelope, _, bound = transport.calls[0]
    assert "AAPL" not in " ".join(argv)
    assert envelope["request"]["symbol"] == "AAPL"
    assert bound <= 2_000_000
    assert "/configured" not in json.dumps(backend.describe())


async def test_backend_identity_mismatch_and_timeout_are_honest(tmp_path):
    adapter, tools = api()
    wrong = arithmetic_chain() | {"symbol": "MSFT"}
    backend = adapter.ExplorerBackend(market_config(), transport=Boundary(wrong))
    result = await tools.explorer_tools(EvidenceStore(tmp_path), backend, None, lambda: NOW)[
        "options_liquidity"
    ]("AAPL")
    assert result["status"] == "invalid_data"

    stopped = asyncio.Event()

    async def wait(*args):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    backend = adapter.ExplorerBackend(market_config(), transport=wait)
    backend.timeout_seconds = 0.01
    result = await tools.explorer_tools(EvidenceStore(tmp_path), backend, None, lambda: NOW)[
        "options_liquidity"
    ]("AAPL")
    assert result["status"] == "retryable_error"
    assert stopped.is_set()


async def test_empty_window_is_unavailable_and_preserves_failed_coverage(tmp_path):
    adapter, tools = api()
    raw = arithmetic_chain()
    raw["chains"][0]["puts"] = []
    raw["available_expiries"].append("2026-11-13")
    raw["selected_expiries"].append("2026-11-13")
    raw["chains"].append({"expiry": "2026-11-13", "status": "unavailable"})
    backend = adapter.ExplorerBackend(market_config(), transport=Boundary(raw))
    store = EvidenceStore(tmp_path)
    result = await tools.explorer_tools(store, backend, None, lambda: NOW)["options_liquidity"](
        "AAPL"
    )
    assert result["status"] == "unavailable"
    assert result["data"]["liquidity_verdict"] == "not_assessed"
    assert result["data"]["profiles"][0]["contracts_in_window"] == 0
    assert result["data"]["coverage"]["empty_expiries"] == ["2026-11-06"]
    assert result["data"]["coverage"]["failed_expiries"] == ["2026-11-13"]
    assert result["data"]["coverage"]["partial_coverage"] is True
    evidence = store.get(result["evidence_refs"][0])
    assert evidence["content"]["summary"]["coverage"] == result["data"]["coverage"]


def test_duplicate_series_cannot_amplify_liquidity_evidence_or_infer_currency():
    adapter, _ = api()
    raw = arithmetic_chain()
    raw["chains"][0]["puts"].append(raw["chains"][0]["puts"][0].copy())
    result = adapter.summarize_options(raw, strike_distance_pct=20)
    puts = next(r for r in result["profiles"] if r["right"] == "put")
    assert result["coverage"]["ambiguous_identity_rows"] == 2
    assert puts["quote_states"].get("two_sided", 0) == 0
    assert puts["spread_median"] is None
    assert puts["currency"] is None
    assert result["units"]["bid_ask_spread"] == "source_contract_currency_per_profile"


async def test_wrong_underlying_quote_is_invalid_data(tmp_path):
    adapter, tools = api()
    raw = arithmetic_chain()
    raw["underlying"]["symbol"] = "MSFT"
    backend = adapter.ExplorerBackend(market_config(), transport=Boundary(raw))
    call = tools.explorer_tools(EvidenceStore(tmp_path), backend, None, lambda: NOW)[
        "options_liquidity"
    ]
    result = await call("AAPL")
    assert result["status"] == "invalid_data"
    assert result["reason"] == "Invalid bounded explorer data"


async def test_malformed_chain_shapes_return_invalid_data_instead_of_tool_exceptions(tmp_path):
    adapter, tools = api()
    raw = arithmetic_chain()
    raw["chains"] = [None]
    backend = adapter.ExplorerBackend(market_config(), transport=Boundary(raw))
    call = tools.explorer_tools(EvidenceStore(tmp_path), backend, None, lambda: NOW)[
        "options_liquidity"
    ]
    result = await call("AAPL")
    assert result["status"] == "invalid_data"
    assert result["reason"] == "Invalid bounded explorer data"
    raw = arithmetic_chain()
    raw["chains"][0]["puts"] = ["bad source row"]
    backend = adapter.ExplorerBackend(market_config(), transport=Boundary(raw))
    call = tools.explorer_tools(EvidenceStore(tmp_path), backend, None, lambda: NOW)[
        "options_liquidity"
    ]
    result = await call("AAPL")
    assert result["status"] == "invalid_data"
    assert result["reason"] == "Invalid bounded explorer data"


async def test_expiry_identity_and_parameterized_evidence_do_not_conflict(tmp_path):
    adapter, tools = api()
    backend = adapter.ExplorerBackend(market_config(), transport=Boundary(arithmetic_chain()))
    call = tools.explorer_tools(EvidenceStore(tmp_path), backend, None, lambda: NOW)[
        "options_liquidity"
    ]
    assert (await call("AAPL", ["2026-11-13"]))["status"] == "invalid_data"
    first = await call("AAPL", None, 20)
    second = await call("AAPL", None, 10)
    assert first["status"] == second["status"] == "success"
    assert first["evidence_refs"] != second["evidence_refs"]


@pytest.mark.parametrize("change", ["bad_time", "wrong_default_expiry"])
async def test_untrusted_timestamp_and_default_expiry_mismatch_are_invalid(tmp_path, change):
    adapter, tools = api()
    raw = arithmetic_chain()
    if change == "bad_time":
        raw["chains"][0]["fetched_at"] = "not-a-timestamp"
    else:
        raw["available_expiries"].insert(0, "2026-11-04")
    backend = adapter.ExplorerBackend(market_config(), transport=Boundary(raw))
    call = tools.explorer_tools(EvidenceStore(tmp_path), backend, None, lambda: NOW)[
        "options_liquidity"
    ]
    result = await call("AAPL")
    assert result["status"] == "invalid_data"
    assert result["reason"] == (
        "Invalid bounded explorer data"
        if change == "bad_time"
        else "Invalid explorer source response"
    )


def test_mixed_option_currencies_do_not_produce_an_absolute_spread_median():
    adapter, _ = api()
    raw = arithmetic_chain()
    raw["chains"][0]["puts"][0]["currency"] = "USD"
    raw["chains"][0]["puts"][1].update({"currency": "EUR", "bid": 1, "ask": 10})
    puts = next(
        p
        for p in adapter.summarize_options(raw, strike_distance_pct=20)["profiles"]
        if p["right"] == "put"
    )
    assert puts["currency"] is None
    assert puts["spread_median"] is None
    assert puts["mixed_currencies"] is True


def test_empty_expiry_is_explicit_even_when_another_expiry_has_data():
    adapter, _ = api()
    raw = arithmetic_chain()
    raw["available_expiries"].append("2026-11-13")
    raw["selected_expiries"].append("2026-11-13")
    raw["chains"].append({"expiry": "2026-11-13", "status": "success", "calls": [], "puts": []})
    result = adapter.summarize_options(raw, strike_distance_pct=20)
    assert result["coverage"]["empty_expiries"] == ["2026-11-13"]
    assert result["coverage"]["partial_coverage"] is True


async def test_captured_public_responses_pass_real_parsing_and_storage(tmp_path):
    adapter, tools = api()
    raw = json.loads((FIXTURES / "helper-options_liquidity.json").read_text())
    backend = adapter.ExplorerBackend(market_config(), transport=Boundary(raw))
    result = await tools.explorer_tools(EvidenceStore(tmp_path / "option"), backend, None)[
        "options_liquidity"
    ]("AAPL", ["2026-11-04", "2026-11-06"])
    assert result["status"] == "success"
    profiles = result["data"]["profiles"]
    assert len(profiles) == 4
    assert [r["contracts_source_count"] for r in profiles] == [38, 44, 71, 70]
    assert all(len(r["sample_contracts"]) <= 4 for r in profiles)
    assert result["data"]["underlying"]["regularMarketTime"] == 1793390401
    assert result["data"]["coverage"]["quote_freshness"].startswith("unknown")

    raw = json.loads((FIXTURES / "helper-market_statistics.json").read_text())
    backend = adapter.ExplorerBackend(market_config(), transport=Boundary(raw))
    ids = [r["series_id"] for r in raw["series"]]
    result = await tools.explorer_tools(
        EvidenceStore(tmp_path / "macro"), backend, None, lambda: NOW
    )["market_statistics"](ids, 365, 3)
    assert result["status"] == "success"
    assert len(result["data"]["series"]) == len(ids)
    assert result["data"]["series"][0]["latest"]["date"] >= "2026-10-28"
    assert result["data"]["partial_coverage"] is False


async def test_captured_tavily_search_preserves_sources_without_fetch(tmp_path):
    _, tools = api()
    raw = json.loads((FIXTURES / "helper-search.json").read_text())
    transport = Boundary(raw)
    research = HermesResearchBackend(
        ResearchBackendConfig(
            python="/configured/python", cwd="/configured/hermes", env_file="/private/env"
        ),
        transport=transport,
    )
    result = await tools.explorer_tools(EvidenceStore(tmp_path), None, research)["public_search"](
        "Apple latest earnings results October 2026", 3
    )
    assert result["status"] == "success"
    assert len(result["data"]["results"]) == 3
    assert all(r["source_url"].startswith("https://") for r in result["data"]["results"])
    assert all(len(r["text"]) <= 800 for r in result["data"]["results"])
    assert transport.calls[0][1]["operation"] == "search"


@pytest.mark.parametrize(
    "observations",
    [
        [{"date": "2026-10-30", "value": 1}, {"date": "2026-10-30", "value": 2}],
        [{"date": "2026-10-30", "value": float("nan")}],
        [{"date": "2026-10-30", "value": True}],
        ["malformed row"],
    ],
)
def test_bad_observations_cannot_become_market_measurements(observations):
    adapter, _ = api()
    with pytest.raises(ValueError):
        adapter.summarize_series(
            "SP500",
            observations,
            start_date="2026-01-01",
            end_date="2026-10-31",
            max_observations=2,
            fetched_at=NOW,
        )
