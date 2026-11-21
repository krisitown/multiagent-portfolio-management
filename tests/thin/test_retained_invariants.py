"""Focused migrations from mixed full-platform tests; no durable service fixtures."""

import hashlib
import json

import pytest

from tests.thin.test_tools import NOW, option
from tests.thin.test_vault import evidence
from wheel_team.tools import evidence_tools, finance_tools
from wheel_team.vault import EvidenceStore, InvalidRecord


async def test_calculation_identity_binds_available_normalized_record_content(tmp_path):
    calculate = finance_tools(EvidenceStore(tmp_path), clock=lambda: NOW)["finance_calculate"]
    result = await calculate(
        "put_economics",
        {"quantity": "1", "premium": "2", "fees": "0"},
        {"contract": option()},
    )
    assert result["status"] == "success"
    content = {key: value for key, value in result["data"].items() if key != "calculation_id"}
    raw = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert result["data"]["calculation_id"] == "calculation:" + hashlib.sha256(raw).hexdigest()


def test_vault_rejects_oversized_records_and_unbounded_queries(tmp_path, monkeypatch):
    from wheel_team import vault

    monkeypatch.setattr(vault, "MAX_RECORD_BYTES", 1024)
    store = EvidenceStore(tmp_path)
    with pytest.raises(InvalidRecord, match="size bound"):
        store.put("evidence", evidence(content={"text": "x" * 2000}))
    assert not list(tmp_path.glob("*.json"))
    with pytest.raises(InvalidRecord, match="bounded"):
        store.find("evidence", {str(index): index for index in range(33)})


async def test_publications_reject_wrong_reference_kinds_and_unresolved_parents(tmp_path):
    store = EvidenceStore(tmp_path)
    ref = store.put("evidence", evidence())
    tools = evidence_tools(store)
    review = await tools["publish_review"](
        {
            "review_id": "bad",
            "run_id": "r",
            "round": 1,
            "reviewed_revision_refs": [ref],
            "conclusion": "wrong kind",
            "created_at": NOW.isoformat(),
            "idempotency_key": "bad",
        }
    )
    assert review["status"] == "invalid_data"
    assert store.find("review", {}) == []
    idea = await tools["publish_idea"](
        {
            "idea_id": "SYN",
            "revision_id": "r2",
            "company_id": "SYN",
            "parent_revision": "idea:SYN@missing",
            "thesis": "Synthetic",
            "countercase": "Risk",
            "status": "Do not execute now — unverified",
            "blocking_reasons": ["No current quote"],
            "author": "analyst",
            "task_id": "job",
            "created_at": NOW.isoformat(),
        }
    )
    assert idea["status"] == "invalid_data"
    assert store.find("idea", {}) == []
