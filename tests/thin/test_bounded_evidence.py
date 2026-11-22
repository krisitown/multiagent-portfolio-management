"""Agent reads never inject complete oversized source envelopes into context."""

import json

from tests.thin.test_vault import evidence
from portfolio_management.tools.evidence import evidence_tools
from portfolio_management.vault import EvidenceStore


async def test_oversized_explorer_read_is_bounded_and_preserves_original(tmp_path):
    store = EvidenceStore(tmp_path / "records")
    original = evidence(content={"report": "Explorer finding with qualification. " * 20000})
    ref = store.put("evidence", original)
    before = store.get(ref)
    result = await evidence_tools(store)["evidence_read"](ref)
    assert len(json.dumps(result)) < 20000
    projection = result["data"]["record"]["tool_response_projection"]
    assert projection["excerpt_only"] is True
    assert projection["omitted_chars"] > 0
    assert projection["stored_record_unchanged"] is True
    assert result["result_refs"] == [ref]
    assert store.get(ref) == before


async def test_full_view_is_rejected(tmp_path):
    store = EvidenceStore(tmp_path / "records")
    ref = store.put("evidence", evidence())
    result = await evidence_tools(store)["evidence_read"](ref, view="full")
    assert result["status"] == "invalid_data"


async def test_oversized_selected_fundamentals_field_is_also_bounded(tmp_path):
    store = EvidenceStore(tmp_path / "records")
    ref = store.put(
        "evidence",
        evidence(
            source="yahoo-fundamentals",
            content={
                "capability": "fundamentals",
                "data": {
                    "financialCurrency": "USD",
                    "marketCap": 123,
                    "longBusinessSummary": "Business description. " * 20000,
                },
            },
        ),
    )
    result = await evidence_tools(store)["evidence_read"](ref)
    assert len(json.dumps(result)) < 20000
    assert result["data"]["record"]["tool_response_projection"]["excerpt_only"]
    assert len(store.get(ref)["content"]["data"]["longBusinessSummary"]) > 100000
