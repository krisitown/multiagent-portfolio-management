"""Immutable run-private records, not a recoverable job platform."""

import importlib
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal

import pytest


def vault_api():
    try:
        return importlib.import_module("portfolio_management.vault")
    except ModuleNotFoundError:
        pytest.fail("thin evidence store is missing")


def evidence(identity="quote-1", **changes):
    return {
        "evidence_id": identity,
        "source": "synthetic",
        "fetched_at": datetime(2026, 10, 31, tzinfo=UTC),
        "source_timestamp": datetime(2026, 10, 29, 20, tzinfo=UTC),
        "source_time_availability": "known",
        "timezone_state": "resolved",
        "delay_state": "end_of_day",
        "content": {"premium": "1.25000000000000000001", "zero": 0, "missing": None},
        **changes,
    }


def test_persisted_reference_detaches_reads_and_keeps_time_and_decimal_text(tmp_path):
    store = vault_api().EvidenceStore(tmp_path / "records")
    payload = evidence()
    ref = store.put("evidence", payload)
    payload["content"]["zero"] = 5
    loaded = vault_api().EvidenceStore(tmp_path / "records").get(ref)
    assert loaded["content"] == {"premium": "1.25000000000000000001", "zero": 0, "missing": None}
    assert loaded["source_timestamp"] == "2026-10-29T20:00:00Z"
    loaded["content"]["zero"] = 9
    assert store.get(ref)["content"]["zero"] == 0
    assert store.put("evidence", evidence()) == ref
    assert stat.S_IMODE((tmp_path / "records").stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600 for path in (tmp_path / "records").glob("*.json")
    )


def test_same_immutable_identity_rejects_conflicting_content(tmp_path):
    api = vault_api()
    store = api.EvidenceStore(tmp_path)
    store.put("evidence", evidence())
    with pytest.raises(api.RecordConflict):
        store.put("evidence", evidence(content={"changed": True}))


def test_local_writers_and_store_instances_do_not_lose_records(tmp_path):
    api = vault_api()
    stores = [api.EvidenceStore(tmp_path) for _ in range(3)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        refs = list(
            executor.map(lambda i: stores[i % 3].put("evidence", evidence(f"q-{i}")), range(30))
        )
    assert len(set(refs)) == 30
    assert len(stores[0].find("evidence", {"source": "synthetic"})) == 30
    assert stores[1].find("evidence", {"source": "absent"}) == []


@pytest.mark.parametrize(
    "unsafe", ["../../outside", "/tmp/secrets", "x/y", "x\\y", "", "x@revision"]
)
def test_identifiers_and_references_never_become_paths(tmp_path, unsafe):
    api = vault_api()
    store = api.EvidenceStore(tmp_path)
    with pytest.raises(api.InvalidRecord):
        store.put("evidence", evidence(unsafe))
    with pytest.raises(api.InvalidRecord):
        store.get(unsafe)


def test_naive_times_nonfinite_decimals_unknown_kinds_and_absent_refs_are_explicit(tmp_path):
    api = vault_api()
    store = api.EvidenceStore(tmp_path)
    for payload in [
        evidence(fetched_at="2026-10-31T00:00:00"),
        evidence(content={"price": Decimal("NaN")}),
    ]:
        with pytest.raises(api.InvalidRecord):
            store.put("evidence", payload)
    with pytest.raises(api.InvalidRecord):
        store.put("../../elsewhere", evidence())
    with pytest.raises(api.RecordNotFound):
        store.get("evidence:missing")


def test_symlink_record_and_symlink_root_are_rejected(tmp_path):
    api = vault_api()
    external = tmp_path / "external"
    external.mkdir()
    link = tmp_path / "link"
    link.symlink_to(external, target_is_directory=True)
    with pytest.raises(api.InvalidRecord):
        api.EvidenceStore(link)
    store = api.EvidenceStore(tmp_path / "safe")
    ref = store.put("evidence", evidence())
    path = next((tmp_path / "safe").glob("*.json"))
    path.unlink()
    path.symlink_to(external / "record.json")
    with pytest.raises(api.InvalidRecord):
        store.get(ref)


def test_conflicting_concurrent_writers_have_one_winner(tmp_path):
    api = vault_api()
    stores = [api.EvidenceStore(tmp_path), api.EvidenceStore(tmp_path)]

    def write(index):
        try:
            return stores[index].put("evidence", evidence(content={"writer": index}))
        except api.RecordConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, range(2)))
    assert results.count("conflict") == 1
    winner = next(ref for ref in results if ref != "conflict")
    assert stores[0].get(winner)["content"]["writer"] in {0, 1}
