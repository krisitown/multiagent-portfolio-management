"""The shared collector is configured explicitly; tests never use SSH or inference."""

import asyncio
import importlib
import json

import pytest


def spark_module():
    return importlib.import_module("wheel_team.adapters.market.spark")


def settings(tmp_path):
    return dict(
        host="example.invalid",
        user="reader",
        identity_file=tmp_path / "key",
        host_key_alias="test-host",
        market_python="/trusted/python",
        research_python="/trusted/research-python",
        scanner_path="/trusted/scanner.py",
        hermes_project="/trusted/hermes",
        hermes_env="/trusted/env",
        capture_root=tmp_path / "captures",
    )


async def test_connected_metadata_and_labeled_numeric_observation_are_preserved(
    tmp_path, monkeypatch
):
    from wheel_team.contracts import MarketQuery

    module = spark_module()
    provider = module.SparkMarketProvider(module.SSHResearchConfig(**settings(tmp_path)))

    async def collect(request):
        return {
            "schema_version": 1,
            "capability": "fundamentals",
            "synthetic": True,
            "fetched_at": "2026-10-31T00:00:00+00:00",
            "source": "synthetic-integrity-test",
            "source_url": "https://example.org/",
            "data": {"synthetic": True, "value": 12.5},
            "limits": ["Synthetic numeric validation only; no company observations"],
        }

    monkeypatch.setattr(provider, "_collect", collect)
    result = await provider.query(
        MarketQuery(query_id="numeric-check", capability="fundamentals", instrument_ids=("TEST",))
    )
    assert result.status == "success"
    assert result.complete is False
    record = result.evidence[0]
    assert record.content["synthetic"] is True
    assert record.content["data"]["value"] == "12.5"
    assert record.fetched_at.isoformat() == "2026-10-31T00:00:00+00:00"
    assert record.source_timestamp is None
    assert record.timezone_state == "unknown"
    metadata = provider.describe()
    assert metadata["mode"] == "connected"
    assert metadata["coverage"]["market_wide"] is False
    assert "fundamentals" in metadata["capabilities"]
    assert str(tmp_path) not in json.dumps(metadata)
    raw = next((tmp_path / "captures").glob("*/raw.json"))
    assert json.loads(raw.read_text())["data"]["value"] == 12.5


@pytest.mark.parametrize(
    "changes",
    [
        {"instrument_ids": ("BAD;exit",)},
        {"parameters": {"path": "/private/unselected"}},
        {"as_of": "2020-01-01T00:00:00Z"},
        {"cursor": "unsupported"},
    ],
)
async def test_invalid_requests_never_reach_ssh(tmp_path, monkeypatch, changes):
    from wheel_team.contracts import MarketQuery

    module = spark_module()
    provider = module.SparkMarketProvider(module.SSHResearchConfig(**settings(tmp_path)))

    async def forbidden(request):
        pytest.fail("Invalid request reached external transport")

    monkeypatch.setattr(provider, "_collect", forbidden)
    result = await provider.query(
        MarketQuery.model_validate(
            {
                "query_id": "input-check",
                "capability": "fundamentals",
                "instrument_ids": ["TEST"],
            }
            | changes
        )
    )
    assert result.status == "invalid_data"
    assert result.evidence == ()
    assert not (tmp_path / "captures").exists()


async def test_ssh_timeout_kills_child_and_sanitizes_error(tmp_path, monkeypatch):
    from wheel_team.contracts import MarketQuery

    module = spark_module()
    provider = module.SparkMarketProvider(module.SSHResearchConfig(**settings(tmp_path)))
    killed = []

    class Process:
        returncode = None

        async def communicate(self, data):
            raise TimeoutError("private-transport-error-must-not-escape")

        def kill(self):
            killed.append(True)

        async def wait(self):
            return 1

    async def create(*args, **kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    result = await provider.query(
        MarketQuery(query_id="timeout", capability="fundamentals", instrument_ids=("TEST",))
    )
    assert killed == [True]
    assert result.status == "retryable_error"
    assert "private-transport-error" not in result.model_dump_json()


def test_private_market_config_rejects_simultaneous_replay_and_connected(tmp_path):
    from wheel_team.config import TeamConfig

    with pytest.raises(ValueError, match="exclusive"):
        TeamConfig.model_validate(
            {
                "model": {"model_id": "offline"},
                "market_backend": settings(tmp_path),
                "market_capture_manifest_path": str(tmp_path / "capture" / "index.json"),
            }
        )


async def test_local_transport_uses_same_helper_stdin_without_shell_or_ssh(tmp_path, monkeypatch):
    module = spark_module()
    local = settings(tmp_path)
    for key in ("host", "user", "identity_file", "host_key_alias"):
        local.pop(key)
    provider = module.SparkMarketProvider(module.SSHResearchConfig(transport="local", **local))
    observed = {}

    class Process:
        returncode = 0

        async def communicate(self, data):
            observed["input"] = json.loads(data)
            return b'{"status":"unavailable"}', None

    async def create(*args, **kwargs):
        observed["argv"] = args
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    request = {"capability": "fundamentals", "symbol": "TEST", "parameters": {}}
    assert await provider._collect(request) == {"status": "unavailable"}
    assert observed["argv"] == ("/trusted/python", "-c", module.BOOTSTRAP)
    assert observed["input"]["request"] == request
    assert observed["input"]["source"] == provider._source


async def test_archived_pep_history_returns_latest_aligned_july_to_october_rows(
    tmp_path, monkeypatch
):
    """Replay actual archived trial evidence; no provider/source request executes."""
    import os
    from pathlib import Path

    from wheel_team.contracts import MarketQuery

    configured = os.environ.get("WHEEL_TEST_HISTORY_RAW")
    if not configured:
        pytest.skip("Select archived PEP trial history with WHEEL_TEST_HISTORY_RAW")
    source_file = Path(configured)
    source_bytes = source_file.read_bytes()
    observation = json.loads(source_bytes)
    assert observation["synthetic"] is False
    assert observation["instrument_id"] == "PEP"
    assert observation["capability"] == "underlying_history"
    frame = observation["data"]
    assert len(frame["index_raw"]) == 251
    module = spark_module()
    provider = module.SparkMarketProvider(module.SSHResearchConfig(**settings(tmp_path)))

    async def archived(request):
        return json.loads(source_bytes)

    monkeypatch.setattr(provider, "_collect", archived)
    result = await provider.query(
        MarketQuery(
            query_id="archived-history",
            capability="underlying_history",
            instrument_ids=("PEP",),
        )
    )
    assert result.status == "success"
    assert result.complete is False
    record = result.evidence[0]
    history = record.content["data"]
    assert history["index_raw"][0] == "2026-07-10 00:00:00-04:00"
    assert history["index_raw"][-1] == "2026-10-30 00:00:00-04:00"
    assert history["index_raw"] == frame["index_raw"][-80:]
    assert history["table"]["index"] == frame["table"]["index"][-80:]
    assert len(history["table"]["data"]) == 80
    for actual, original in zip(
        history["table"]["data"], frame["table"]["data"][-80:], strict=True
    ):
        assert actual == [
            str(value) if type(value) in (int, float) else value for value in original
        ]
    coverage = history["coverage"]
    assert coverage["original_rows"] == "251"
    assert coverage["returned_rows"] == "80"
    assert coverage["omitted_rows"] == "171"
    assert coverage["ordering"] == "chronological_ascending"
    assert coverage["selection"] == "most_recent_rows"
    assert coverage["returned_first_index_raw"] == "2026-07-10 00:00:00-04:00"
    assert coverage["returned_last_index_raw"] == "2026-10-30 00:00:00-04:00"
    assert coverage["full_raw_retained"] is True
    assert record.source_timestamp is None
    assert record.timezone_state == "unknown"
    assert record.fetched_at.isoformat() == observation["fetched_at"]
    truncation = next(
        item for item in record.content["truncation"] if item["field"] == "underlying_history"
    )
    assert truncation["original_items"] == 251
    assert truncation["returned_items"] == 80
    assert truncation["full_raw_retained"] is True
    assert next((tmp_path / "captures").glob("*/raw.json")).read_bytes() == source_bytes
    assert source_file.read_bytes() == source_bytes
