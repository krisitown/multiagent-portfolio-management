"""Synthetic isolated raw-chain boundary fixtures; never source or account data."""

import importlib
import io
import json
import re
import types

import pytest
from test_market_spark import settings

from wheel_team.contracts import MarketQuery

PARAMS = {
    "contracts": [
        {"expiry": "2026-11-06", "option_right": "put", "strike": "110"},
        {"expiry": "2027-01-15", "option_right": "put", "strike": "95.5"},
        {"expiry": "2027-01-15", "option_right": "call", "strike": "95.5"},
        {"expiry": "2027-01-15", "option_right": "call", "strike": "120"},
    ],
    "include_expiries": True,
}


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    """Replace only external raw source operations; real collector + adapter run."""
    collector = importlib.import_module("wheel_team.adapters.market.collector")
    calls = []
    rows = [
        {
            "option": "TEST261106P00110000",
            "bid": 10,
            "ask": 11,
            "quote_time": "2026-10-30 15:59:00",
            "last_trade_time": "2026-10-30T19:50:00Z",
        },
        {"option": "TEST270115P00095500", "bid": None, "ask": 2.5},
        {"option": "TEST270115C00095500", "bid": 8, "ask": 9},
        {"option": "TEST261106C00110000", "bid": 77, "ask": 78},
        {"option": "TEST270115P00110000", "bid": 88, "ask": 89},
    ]
    payload = {"timestamp": "2026-10-30 16:00:00", "data": {"current_price": 100, "options": rows}}

    def urlopen(request, timeout):
        assert (
            request.full_url == "https://cdn.cboe.com/api/global/delayed_quotes/options/TEST.json"
        )
        assert timeout == 25
        calls.append("cboe")
        if isinstance(payload, Exception):
            raise payload
        return io.BytesIO(json.dumps(payload).encode())

    scanner = tmp_path / "scanner.py"
    scanner.write_text("# Synthetic boundary scanner; production scanner is untouched.\n")
    namespace = {
        "CBOE_URL": "https://cdn.cboe.com/api/global/delayed_quotes/options/{tk}.json",
        "OPT_RE": re.compile(r"^[A-Z\.]+?(\d{6})([CP])(\d{8})$"),
        "urllib": types.SimpleNamespace(
            request=types.SimpleNamespace(
                Request=__import__("urllib.request", fromlist=["Request"]).Request, urlopen=urlopen
            )
        ),
        "scan_ticker": lambda *args: {"chain": [{"strike": 90}], "expiry": "2026-12-04"},
        "finish_candidate": lambda candidate, cfg: candidate,
    }
    # Trusted file exec is isolated at compile/exec boundary, with no source I/O.
    original_exec = exec

    def scanner_exec(code, ns):
        original_exec(code, ns)
        ns.update(namespace)

    monkeypatch.setattr(collector, "exec", scanner_exec, raising=False)

    class Frame:
        def __init__(self, records):
            self.records = records

        def to_dict(self, orient):
            assert orient == "records"
            return self.records

    class Ticker:
        options = ("2026-11-06", "2027-01-15")

        def option_chain(self, expiry):
            calls.append(("yahoo", expiry))
            return types.SimpleNamespace(
                puts=Frame(
                    [
                        {
                            "contractSymbol": {
                                "2026-11-06": "TEST261106P00110000",
                                "2027-01-15": "TEST270115P00095500",
                            }[expiry],
                            "strike": 110 if expiry == "2026-11-06" else 95.5,
                            "bid": 1,
                            "ask": 2,
                            "lastTradeDate": "2026-10-30T19:00:00Z",
                        }
                    ]
                ),
                calls=Frame(
                    [
                        {
                            "contractSymbol": {
                                "2026-11-06": "TEST261106C00095500",
                                "2027-01-15": "TEST270115C00095500",
                            }[expiry],
                            "strike": 95.5,
                            "bid": 3,
                            "ask": 4,
                        }
                    ]
                ),
            )

    monkeypatch.setitem(
        __import__("sys").modules,
        "yfinance",
        types.SimpleNamespace(
            Ticker=lambda symbol: Ticker(), set_tz_cache_location=lambda path: None
        ),
    )
    return collector, {"scanner_path": str(scanner)}, calls, payload


async def test_exact_old_new_roll_legs_calls_itm_and_missing_are_isolated(
    boundary, tmp_path, monkeypatch
):
    collector, config, calls, payload = boundary
    spark = importlib.import_module("wheel_team.adapters.market.spark")
    provider = spark.SparkMarketProvider(spark.SSHResearchConfig(**settings(tmp_path)))

    async def collect(request):
        return await collector.collect(request, config)

    monkeypatch.setattr(provider, "_collect", collect)
    result = await provider.query(
        MarketQuery(
            query_id="roll", capability="option_chains", instrument_ids=("TEST",), parameters=PARAMS
        )
    )
    assert result.status == "success"
    assert result.complete is False
    record = result.evidence[0]
    data = record.content["data"]
    assert [row["status"] for row in data["contracts"]] == ["found", "found", "found", "missing"]
    assert [row["bid"] for row in data["contracts"]] == ["10", None, "8", None]
    assert data["contracts"][0]["expiry"] == "2026-11-06"
    assert data["contracts"][1]["strike"] == "95.5"
    assert data["contracts"][2]["option_right"] == "call"
    assert data["contracts"][0]["quote_time_raw"] == "2026-10-30 15:59:00"
    assert data["contracts"][0]["last_trade_time_raw"] == "2026-10-30T19:50:00Z"
    assert data["contracts"][1]["quote_time_raw"] is None
    assert data["contracts"][1]["delta"] is None
    assert "bid" in data["contracts"][1]["unknown_fields"]
    assert data["snapshot_time_raw"] == "2026-10-30 16:00:00"
    assert data["available_expiries"] == ["2026-11-06", "2027-01-15"]
    assert record.source_timestamp is None
    assert record.source_time_raw is None
    assert record.timezone_state == "unknown"
    assert calls == ["cboe"]
    assert len(data["scanner_source_sha256"]) == 64
    assert next((tmp_path / "captures").glob("*/raw.json")).is_file()


async def test_metadata_only_is_bounded_and_reports_omissions(boundary):
    collector, config, calls, payload = boundary
    # 108 valid expiries across months/years, independent of discovery DTE windows.
    payload["data"]["options"] = [
        {"option": f"TEST{year:02d}{month:02d}01P00095500"}
        for year in range(27, 36)
        for month in range(1, 13)
    ]
    result = await collector.collect(
        {"capability": "option_chains", "symbol": "TEST", "parameters": {"include_expiries": True}},
        config,
    )
    data = result["data"]
    assert len(data["available_expiries"]) == 80
    assert data["expiry_coverage"] == {"total": 108, "returned": 80, "omitted": 28}
    assert data["contracts"] == []
    assert calls == ["cboe"]


async def test_parameterless_discovery_retains_legacy_shape(boundary):
    collector, config, calls, payload = boundary
    result = await collector.collect(
        {"capability": "option_chains", "symbol": "TEST", "parameters": {}}, config
    )
    assert result["data"]["candidate"] == {"chain": [{"strike": 90}], "expiry": "2026-12-04"}
    assert result["data"]["selection_constraints"]["dte_min"] == 30
    assert calls == []


async def test_yahoo_fallback_keeps_trade_time_separate_and_source_errors_private(boundary):
    collector, config, calls, payload = boundary

    # Source failure at the external I/O boundary; exception text must never escape.
    def fail(*args, **kwargs):
        raise RuntimeError("/private/account/secret must not escape")

    original_exec = collector.exec

    def broken_cboe(code, ns):
        original_exec(code, ns)
        ns["urllib"].request.urlopen = fail

    collector.exec = broken_cboe
    result = await collector.collect(
        {"capability": "option_chains", "symbol": "TEST", "parameters": PARAMS}, config
    )
    data = result["data"]
    assert data["source_errors"] == {"cboe": "source_error"}
    assert [row["status"] for row in data["contracts"]] == ["found", "found", "found", "missing"]
    assert [row["bid"] for row in data["contracts"]] == [1, 1, 3, None]
    assert data["contracts"][0]["quote_time_raw"] is None
    assert data["contracts"][0]["last_trade_time_raw"] == "2026-10-30T19:00:00Z"
    assert data["contracts"][0]["delta"] is None
    assert calls == [("yahoo", "2026-11-06"), ("yahoo", "2027-01-15")]
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "params",
    [
        {"contracts": []},
        {"contracts": PARAMS["contracts"] * 5},
        {"contracts": [PARAMS["contracts"][0]] * 2},
        {"contracts": [{"expiry": "2026-02-30", "option_right": "put", "strike": "10"}]},
        {"contracts": [{"expiry": "20261106", "option_right": "put", "strike": "10"}]},
        *[
            {"contracts": [{"expiry": "2026-11-06", "option_right": "put", "strike": strike}]}
            for strike in [True, 110, "NaN", "Infinity", "1e2", "0", "-1", "01", "100000", "1.0001"]
        ],
        {"contracts": [{"expiry": "2026-11-06", "right": "put", "strike": "10"}]},
        {"contracts": [{"expiry": "2026-11-06", "option_right": "P", "strike": "10"}]},
        {"include_expiries": False},
        {"include_expiries": 1},
        {"expiries": ["2026-11-06"]},
        {
            "contracts": [
                {"expiry": f"2027-0{month}-01", "option_right": "put", "strike": "10"}
                for month in range(1, 6)
            ]
        },
    ],
)
async def test_invalid_exact_requests_never_reach_transport(params, tmp_path, monkeypatch):
    spark = importlib.import_module("wheel_team.adapters.market.spark")
    provider = spark.SparkMarketProvider(spark.SSHResearchConfig(**settings(tmp_path)))

    async def forbidden(request):
        pytest.fail("Invalid exact request reached source transport")

    monkeypatch.setattr(provider, "_collect", forbidden)
    result = await provider.query(
        MarketQuery(
            query_id="invalid-exact",
            capability="option_chains",
            instrument_ids=("TEST",),
            parameters=params,
        )
    )
    assert result.status == "invalid_data"


async def test_valid_limits_and_decimal_boundaries_are_accepted(tmp_path, monkeypatch):
    spark = importlib.import_module("wheel_team.adapters.market.spark")
    provider = spark.SparkMarketProvider(spark.SSHResearchConfig(**settings(tmp_path)))
    selected = []

    async def collect(request):
        selected.append(request)
        return {"status": "unavailable"}

    monkeypatch.setattr(provider, "_collect", collect)
    contracts = [
        {"expiry": f"2027-0{month}-01", "option_right": "put", "strike": strike}
        for month in range(1, 5)
        for strike in ("0.001", "1.000", "10.5", "99999.999")
    ]
    result = await provider.query(
        MarketQuery(
            query_id="max-exact",
            capability="option_chains",
            instrument_ids=("TEST",),
            parameters={"contracts": contracts},
        )
    )
    assert result.status == "unavailable"  # external boundary reached after validation
    assert len(selected) == 1
    assert len(selected[0]["parameters"]["contracts"]) == 16


async def test_all_source_failures_report_each_requested_contract_unavailable(
    boundary, monkeypatch
):
    collector, config, calls, payload = boundary

    def fail(*args, **kwargs):
        raise RuntimeError("private source failure")

    original_exec = collector.exec

    def broken_cboe(code, ns):
        original_exec(code, ns)
        ns["urllib"].request.urlopen = fail

    monkeypatch.setattr(collector, "exec", broken_cboe)
    monkeypatch.setitem(
        __import__("sys").modules,
        "yfinance",
        types.SimpleNamespace(
            Ticker=lambda symbol: types.SimpleNamespace(option_chain=fail),
            set_tz_cache_location=lambda path: None,
        ),
    )
    result = await collector.collect(
        {"capability": "option_chains", "symbol": "TEST", "parameters": PARAMS}, config
    )
    assert [row["status"] for row in result["data"]["contracts"]] == ["unavailable"] * 4
    assert result["data"]["source_errors"] == {
        "cboe": "source_error",
        "yahoo_expiries": "source_error",
        "yahoo_chain:2026-11-06": "source_error",
        "yahoo_chain:2027-01-15": "source_error",
    }
    assert result["complete"] is False
    assert "private source" not in json.dumps(result)


async def test_raw_zero_is_preserved_foreign_roots_skipped_and_duplicate_series_are_ambiguous(
    boundary,
):
    collector, config, calls, payload = boundary
    payload["data"]["options"] = [
        {"option": "TEST261106P00110000", "bid": 0, "ask": 0},
        {"option": "OTHER270115P00095500", "bid": 99, "ask": 100},
        {"option": "TEST270115C00095500", "bid": 1, "ask": 2},
        {"option": "TEST270115C00095500", "bid": 3, "ask": 4},
    ]
    result = await collector.collect(
        {"capability": "option_chains", "symbol": "TEST", "parameters": PARAMS}, config
    )
    rows = result["data"]["contracts"]
    assert rows[0]["bid"] == 0
    assert rows[0]["ask"] == 0
    assert rows[1]["status"] == "missing"
    assert rows[1]["bid"] is None
    assert rows[2]["status"] == "ambiguous"
    assert rows[2]["bid"] is None
    assert result["data"]["skipped_source_rows"] == 1


@pytest.mark.parametrize(
    "source_symbol",
    [
        "OTHER261106P00110000",  # Different underlying root.
        "OTHER270115C00110000",  # Reviewer's cross-root/date/right counterexample.
        "TEST1261106P00110000",  # Adjusted/unsupported numeric root.
        "TEST270115P00110000",  # Different expiry.
        "TEST261106C00110000",  # Call in the puts frame.
        "TEST261106P00111000",  # Symbol strike disagrees with numeric strike110.
        "TEST261132P00110000",  # Malformed calendar date.
        "not-an-option-symbol",
        None,
    ],
)
async def test_yahoo_fallback_rejects_unverified_contract_symbol_identity(
    boundary, monkeypatch, source_symbol
):
    collector, config, calls, payload = boundary

    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic CBOE source failure")

    original_exec = collector.exec

    def broken_cboe(code, ns):
        original_exec(code, ns)
        ns["urllib"].request.urlopen = fail

    monkeypatch.setattr(collector, "exec", broken_cboe)
    raw = {"strike": 110, "bid": 77, "ask": 78}
    if source_symbol is not None:
        raw["contractSymbol"] = source_symbol

    class Frame:
        def __init__(self, rows):
            self.rows = rows

        def to_dict(self, orient):
            return self.rows

    ticker = types.SimpleNamespace(
        option_chain=lambda expiry: types.SimpleNamespace(puts=Frame([raw]), calls=Frame([]))
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "yfinance",
        types.SimpleNamespace(
            Ticker=lambda symbol: ticker, set_tz_cache_location=lambda path: None
        ),
    )
    result = await collector.collect(
        {
            "capability": "option_chains",
            "symbol": "TEST",
            "parameters": {"contracts": [PARAMS["contracts"][0]]},
        },
        config,
    )
    row = result["data"]["contracts"][0]
    assert row["status"] == "missing"
    assert row["bid"] is None
    assert row["ask"] is None
    assert {"bid", "ask", "source_contract_symbol"} <= set(row["unknown_fields"])
    assert result["data"]["skipped_source_rows"] == 1
    assert result["data"]["source_errors"] == {"cboe": "source_error"}
    assert result["complete"] is False
