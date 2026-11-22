"""Capture integrity checks use labelled validation bytes, never market observations."""

import hashlib
import importlib
from pathlib import Path

import pytest


def verified_file(root: Path, name: str, digest: str) -> bytes:
    module = importlib.import_module("portfolio_management.adapters.market.captured")
    return module._read_verified_file(root, name, digest)


def test_capture_file_hash_is_checked_before_bytes_are_used(tmp_path):
    payload = b"synthetic integrity-test bytes; no company observations"
    (tmp_path / "evidence.json").write_bytes(payload)
    assert verified_file(tmp_path, "evidence.json", hashlib.sha256(payload).hexdigest()) == payload
    with pytest.raises(ValueError, match="hash"):
        verified_file(tmp_path, "evidence.json", "0" * 64)


@pytest.mark.parametrize("reference", ["../outside.json", "/outside.json"])
def test_capture_references_cannot_escape_selected_capture(tmp_path, reference):
    with pytest.raises(ValueError, match="path"):
        verified_file(tmp_path, reference, "0" * 64)


def test_capture_symlink_cannot_reference_another_capture(tmp_path):
    selected = tmp_path / "selected"
    selected.mkdir()
    outside = tmp_path / "other-capture.json"
    outside.write_bytes(b"synthetic integrity-test bytes")
    (selected / "evidence.json").symlink_to(outside)
    with pytest.raises(ValueError, match="path"):
        verified_file(selected, "evidence.json", hashlib.sha256(outside.read_bytes()).hexdigest())


@pytest.fixture
def real_capture_index():
    import os

    configured = os.environ.get("WHEEL_TEST_CAPTURE_INDEX")
    if not configured:
        pytest.skip("Select completed private evidence with WHEEL_TEST_CAPTURE_INDEX")
    return Path(configured)


def provider_for(path):
    module = importlib.import_module("portfolio_management.adapters.market.captured")
    return module.CapturedMarketProvider(path)


async def test_real_capture_preserves_fundamentals_and_public_scope(real_capture_index):
    import json

    from portfolio_management.contracts import MarketQuery

    provider = provider_for(real_capture_index)
    result = await provider.query(
        MarketQuery(
            query_id="real-fundamentals", capability="fundamentals", instrument_ids=("MSFT",)
        )
    )
    original = json.loads((real_capture_index.parent / "evidence-MSFT-info.json").read_text())
    assert result.status == "success"
    assert result.complete is False
    assert len(result.evidence) == 1
    assert result.evidence[0].model_dump(mode="json") == original
    assert result.evidence[0].content["fields"]["longName"] == "Microsoft Corporation"
    assert provider.describe()["mode"] == "captured_replay"
    assert provider.describe()["coverage"]["captured"] == ["KO", "MSFT", "JNJ"]
    assert provider.describe()["coverage"]["market_wide"] is False
    assert set(provider.capabilities()) == {
        "fundamentals",
        "underlying_quotes",
        "events",
        "option_chains",
        "universe",
    }


async def test_real_capture_retains_unknown_ko_timezone_and_eod_data(real_capture_index):
    from portfolio_management.contracts import MarketQuery

    result = await provider_for(real_capture_index).query(
        MarketQuery(query_id="ko-quotes", capability="underlying_quotes", instrument_ids=("KO",))
    )
    cboe = next(record for record in result.evidence if record.source == "cboe-delayed-options")
    assert cboe.source_timestamp is None
    assert cboe.timezone_state == "unknown"
    assert cboe.delay_state == "delayed"
    assert cboe.source_time_raw == "2026-10-30 23:46:25"
    assert cboe.fetched_at.isoformat() == "2026-10-30T23:47:01.190558+00:00"
    yahoo = next(record for record in result.evidence if record.source == "yahoo-info")
    assert yahoo.delay_state == "end_of_day"
    assert yahoo.source_timestamp.isoformat() == "2026-10-30T20:00:02+00:00"


async def test_capture_pages_do_not_turn_missing_coverage_into_complete(real_capture_index):
    from portfolio_management.contracts import MarketQuery

    provider = provider_for(real_capture_index)
    query = MarketQuery(
        query_id="pages", capability="underlying_quotes", instrument_ids=("KO",), limit=1
    )
    ids = []
    while True:
        result = await provider.query(query)
        assert result.complete is False
        ids.extend(record.evidence_id for record in result.evidence)
        if result.next_cursor is None:
            break
        query = query.model_copy(update={"cursor": result.next_cursor})
    assert len(ids) == len(set(ids)) == 3
    absent = await provider.query(
        MarketQuery(query_id="missing", capability="fundamentals", instrument_ids=("AMZN",))
    )
    assert absent.status == "unavailable"
    assert absent.evidence == ()
    assert absent.complete is False


@pytest.mark.parametrize(
    "changed", ["evidence-MSFT-info.json", "MSFT-yahoo-info.json", "collection.json"]
)
def test_changed_completed_capture_files_are_rejected(real_capture_index, tmp_path, changed):
    import shutil

    destination = tmp_path / real_capture_index.parent.name
    shutil.copytree(real_capture_index.parent, destination, copy_function=shutil.copyfile)
    with (destination / changed).open("ab") as handle:
        handle.write(b"\nchanged integrity-test bytes")
    with pytest.raises(ValueError, match="hash"):
        provider_for(destination / real_capture_index.name)


def test_capture_without_finished_collection_is_rejected(real_capture_index, tmp_path):
    import json
    import shutil

    destination = tmp_path / real_capture_index.parent.name
    shutil.copytree(real_capture_index.parent, destination, copy_function=shutil.copyfile)
    collection = json.loads((destination / "collection.json").read_text())
    collection.pop("finished_at")
    payload = json.dumps(collection).encode()
    (destination / "collection.json").write_bytes(payload)
    index = json.loads((destination / "index.json").read_text())
    index["collection_sha256"] = hashlib.sha256(payload).hexdigest()
    (destination / "index.json").write_text(json.dumps(index))
    with pytest.raises(ValueError, match="finished"):
        provider_for(destination / "index.json")


def test_capture_index_cannot_relabel_another_company_observation(real_capture_index, tmp_path):
    import json
    import shutil

    destination = tmp_path / real_capture_index.parent.name
    shutil.copytree(real_capture_index.parent, destination, copy_function=shutil.copyfile)
    index = json.loads((destination / "index.json").read_text())
    index["entries"][0]["instrument_id"] = "AMZN"
    (destination / "index.json").write_text(json.dumps(index))
    with pytest.raises(ValueError, match="identity"):
        provider_for(destination / "index.json")
