"""Retained internal evidence tools preserve exact references and compact discovery."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from strands import Agent, tool

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_vault import evidence
from portfolio_management.tools.evidence import evidence_tools
from portfolio_management.types import TeamResult
from portfolio_management.vault import EvidenceStore, InvalidRecord


async def test_sdk_discovery_surfaces_record_kind_enum_and_compact_canonical_reference(tmp_path):
    store = EvidenceStore(tmp_path / "records")
    store.put(
        "evidence",
        evidence(
            content={
                "capability": "underlying_quotes",
                "instrument_id": "MECHANICAL_TEST",
                "data": {"isolated_payload": "body must not enter discovery" * 200},
            }
        ),
    )
    model = ScriptModel(
        output("evidence_find", kind="evidence"),
        output("TeamResult", summary="Offline index inspected"),
    )
    agent = Agent(
        model=model,
        tools=[tool(fn) for fn in evidence_tools(store).values()],
        callback_handler=None,
    )
    await agent.invoke_async("Inspect run-local index", structured_output_model=TeamResult)
    spec = next(x for x in model.calls[0]["tools"] if x["name"] == "evidence_find")
    schema = spec["inputSchema"]["json"]["properties"]
    assert set(schema["kind"]["enum"]) == {
        "evidence",
        "calculation",
        "snapshot",
        "idea",
        "review",
        "quality",
    }
    assert "evidence_read" in spec["description"]
    result = model.calls[1]["messages"][-1]["content"][0]["toolResult"]
    data = json.loads(result["content"][0]["text"])
    assert data["result_refs"] == ["evidence:quote-1"]
    row = data["data"]["records"][0]
    assert row["record_ref"] == "evidence:quote-1"
    assert data["data"]["projection"]["kind"] == "evidence"
    assert row["metadata"]["content"]["capability"] == "underlying_quotes"
    assert "body_omitted" not in row
    assert "selected_fields" not in row
    assert "kind" not in row
    assert data["data"]["projection"]["body_omitted"] is True
    assert data["data"]["projection"]["bounded_record_tool"] == "evidence_read"
    assert "content.data" in row["omitted_fields"]
    assert "isolated_payload" not in json.dumps(data)


@pytest.mark.parametrize(
    "kind", ["quote", "market_observation", "research_result", "underlying_quotes", "option_chains"]
)
async def test_observed_invalid_kinds_return_discovery_guidance_without_guessing(tmp_path, kind):
    api = evidence_tools(EvidenceStore(tmp_path / "records"))
    result = await api["evidence_find"](kind)
    assert result["status"] == "invalid_data"
    assert "evidence" in result["reason"] and "idea" in result["reason"]
    assert 'kind="evidence"' in result["reason"]
    assert result["result_refs"] == []


@pytest.mark.parametrize(
    "record_ref",
    [
        "live-20261031T012516Z-104dc1874f63-underlying_quotes",
        "public-web:4de6bd6b908323dc9edc10ab1b1b2156be5087d5e095519c8226499334ae7aeb",
        "/etc/passwd",
    ],
)
async def test_sdk_malformed_read_is_actionable_and_does_not_auto_prefix(tmp_path, record_ref):
    store = EvidenceStore(tmp_path / "records")
    store.put("evidence", evidence("live-20261031T012516Z-104dc1874f63-underlying_quotes"))
    model = ScriptModel(
        output("evidence_read", record_ref=record_ref),
        output("TeamResult", summary="Read rejected"),
    )
    agent = Agent(
        model=model,
        tools=[tool(fn) for fn in evidence_tools(store).values()],
        callback_handler=None,
    )
    await agent.invoke_async("Read exact reference", structured_output_model=TeamResult)
    result = model.calls[1]["messages"][-1]["content"][0]["toolResult"]
    data = json.loads(result["content"][0]["text"])
    assert data["status"] == "invalid_data"
    assert "evidence_find" in data["reason"] and "evidence:" in data["reason"]
    assert data["data"] is None and data["result_refs"] == []


async def test_discovery_pages_every_record_and_full_read_preserves_exact_payload(tmp_path):
    store = EvidenceStore(tmp_path / "records")
    refs = {
        store.put("evidence", evidence(f"mechanical-{i}", content={"test_integer": i}))
        for i in range(23)
    }
    api = evidence_tools(store)
    first = await api["evidence_find"]("evidence")
    assert first["data"]["total"] == 23
    assert first["data"]["returned"] == 20
    assert first["data"]["next_offset"] == 20
    assert first["data"]["selection"] == "exact_top_level_query_then_store_order_offset_limit"
    second = await api["evidence_find"]("evidence", offset=20)
    assert second["data"]["returned"] == 3
    assert second["data"]["next_offset"] is None
    assert set(first["result_refs"] + second["result_refs"]) == refs
    for ref in refs:
        read = await api["evidence_read"](ref)
        assert read["data"]["record_ref"] == ref
        assert read["result_refs"] == [ref]
        assert read["data"]["record"] == store.get(ref)


@pytest.mark.parametrize(
    "kwargs", [{"limit": 0}, {"limit": 51}, {"offset": -1}, {"limit": True}, {"offset": "1"}]
)
async def test_discovery_rejects_invalid_bounds(tmp_path, kwargs):
    result = await evidence_tools(EvidenceStore(tmp_path))["evidence_find"]("evidence", **kwargs)
    assert result["status"] == "invalid_data"
    assert "limit" in result["reason"] and "offset" in result["reason"]


async def test_index_metadata_strings_and_lists_are_bounded_and_explicitly_marked(tmp_path):
    store = EvidenceStore(tmp_path / "records")
    ref = store.put(
        "evidence",
        evidence(
            source_url="https://example.invalid/" + "x" * 10000,
            coverage=["c" * 1000 for _ in range(100)],
            content={
                "title": "t" * 10000,
                "capability": "test",
                "instrument_id": "ISOLATED_TEST",
                "data": "private body",
            },
        ),
    )
    result = await evidence_tools(store)["evidence_find"]("evidence")
    row = result["data"]["records"][0]
    assert {"source_url", "coverage", "content.title"} <= set(row["truncated_fields"])
    assert len(row["metadata"]["source_url"]) <= 1024
    assert len(row["metadata"]["coverage"]) <= 8
    assert len(row["metadata"]["content"]["title"]) <= 256
    assert "title" in result["data"]["projection"]["content_fields"]
    assert (
        result["data"]["projection"]["content_selection"]
        == "allowlisted_string_fields_only_if_present"
    )
    assert len(json.dumps(row)) < 6000
    assert store.get(ref)["content"]["title"] == "t" * 10000


def test_public_reference_helper_uses_validated_immutable_revision_identity():
    from portfolio_management import vault

    helper = getattr(vault, "record_reference", None)
    assert callable(helper), "Shared canonical reference helper is missing"
    idea = {
        "idea_id": "isolated-contract",
        "revision_id": "r2",
        "company_id": "MECHANICAL_TEST",
        "thesis": "Isolated schema test",
        "countercase": "No issuer facts",
        "status": "Ready for user review",
        "author": "test",
        "task_id": "test",
        "created_at": "2026-10-31T00:00:00Z",
    }
    assert helper("idea", idea) == "idea:isolated-contract@r2"
    with pytest.raises(InvalidRecord):
        helper("idea", idea | {"revision_id": "../../bad"})
    with pytest.raises(InvalidRecord):
        helper("quote", evidence())


async def test_original_attempt4_index_is_smaller_and_canonical_refs_resolve_without_source_changes(
    tmp_path,
):
    selected = os.environ.get("WHEEL_TEST_EVIDENCE_RUN")
    if not selected:
        pytest.skip("Set WHEEL_TEST_EVIDENCE_RUN to the immutable private attempt-4 run")
    paths = sorted((Path(selected) / "records").glob("*.json"))
    assert len(paths) == 18
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    store = EvidenceStore(tmp_path / "records")
    originals = {}
    for p in paths:
        envelope = json.loads(p.read_text())
        assert envelope["kind"] == "evidence"
        assert store.put("evidence", envelope["record"]) == envelope["ref"]
        originals[envelope["ref"]] = envelope["record"]
    api = evidence_tools(store)
    index = await api["evidence_find"]("evidence")
    assert len(index["data"]["records"]) == 18
    assert len(json.dumps(index)) < len(json.dumps(list(originals.values()))) * 0.5
    assert set(index["result_refs"]) == set(originals)
    for row in index["data"]["records"]:
        full = await api["evidence_read"](row["record_ref"])
        assert full["data"]["record"] == originals[row["record_ref"]]
        assert full["data"]["record"]["source_url"] == row["metadata"]["source_url"]
    history = next(
        row
        for row in index["data"]["records"]
        if row["metadata"].get("content", {}).get("capability") == "underlying_history"
    )
    assert history["metadata"]["content"]["instrument_id"] == "PEP"
    assert "data" not in history["metadata"]["content"]
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths} == before


async def test_discovery_bounds_omitted_field_names_without_hiding_omission_count(tmp_path):
    store = EvidenceStore(tmp_path / "records")
    store.put(
        "evidence", evidence(content={f"unknown-{i}-" + "x" * 2000: "omitted" for i in range(100)})
    )
    result = await evidence_tools(store)["evidence_find"]("evidence")
    row = result["data"]["records"][0]
    assert len(row["omitted_fields"]) <= 32
    assert all(len(path) <= 256 for path in row["omitted_fields"])
    assert row["omitted_field_count"] > 100
    assert row["omitted_fields_truncated"] is True
    assert len(json.dumps(row)) < 12000


@pytest.mark.parametrize(
    "kind", ["quote", "market_observation", "research_result", "underlying_quotes", "option_chains"]
)
async def test_sdk_rejects_observed_invalid_kind_then_can_discover_exact_reference(tmp_path, kind):
    store = EvidenceStore(tmp_path / "records")
    store.put("evidence", evidence())
    model = ScriptModel(
        output("evidence_find", kind=kind),
        output("evidence_find", kind="evidence"),
        output("TeamResult", summary="Explicit kind correction"),
    )
    agent = Agent(
        model=model,
        tools=[tool(fn) for fn in evidence_tools(store).values()],
        callback_handler=None,
    )
    await agent.invoke_async("Inspect records", structured_output_model=TeamResult)
    rejected = model.calls[1]["messages"][-1]["content"][0]["toolResult"]
    assert rejected["status"] == "error"
    assert "evidence" in json.dumps(rejected) and "quality" in json.dumps(rejected)
    discovered = model.calls[2]["messages"][-1]["content"][0]["toolResult"]
    assert json.loads(discovered["content"][0]["text"])["result_refs"] == ["evidence:quote-1"]


@pytest.mark.parametrize(
    "kind,record,expected_ref",
    [
        (
            "calculation",
            {
                "calculation_id": "calc-test",
                "status": "unavailable",
                "formula": "isolated",
                "formula_version": "1",
            },
            "calculation:calc-test",
        ),
        (
            "snapshot",
            {
                "snapshot_id": "snapshot-test",
                "account_alias": "isolated",
                "source": "isolated",
                "as_of": "2026-10-31T00:00:00Z",
                "fetched_at": "2026-10-31T00:00:00Z",
                "base_currency": "USD",
                "completeness": "unknown",
            },
            "snapshot:snapshot-test",
        ),
        (
            "idea",
            {
                "idea_id": "idea-test",
                "revision_id": "r1",
                "company_id": "MECHANICAL_TEST",
                "thesis": "Isolated schema test",
                "countercase": "No issuer facts",
                "status": "Ready for user review",
                "author": "test",
                "task_id": "test",
                "created_at": "2026-10-31T00:00:00Z",
            },
            "idea:idea-test@r1",
        ),
        (
            "review",
            {
                "review_id": "review-test",
                "run_id": "run-test",
                "round": 1,
                "reviewed_revision_refs": [],
                "conclusion": "Isolated schema test",
                "created_at": "2026-10-31T00:00:00Z",
                "idempotency_key": "review-test",
            },
            "review:review-test",
        ),
        (
            "quality",
            {
                "assessment_id": "quality-test",
                "company_id": "MECHANICAL_TEST",
                "assessed_at": "2026-10-31T00:00:00Z",
                "conclusion": "unverified",
                "rationale": "No issuer facts",
            },
            "quality:quality-test",
        ),
    ],
)
async def test_non_evidence_kinds_use_canonical_immutable_identity_without_full_body(
    tmp_path, kind, record, expected_ref
):
    store = EvidenceStore(tmp_path / "records")
    store.put(kind, record)
    tools = evidence_tools(store)
    index = await tools["evidence_find"](kind)
    assert index["result_refs"] == [expected_ref]
    row = index["data"]["records"][0]
    assert row["record_ref"] == expected_ref
    assert index["data"]["projection"]["kind"] == kind
    assert index["data"]["projection"]["body_omitted"] is True
    assert "kind" not in row and "selected_fields" not in row and "body_omitted" not in row
    assert "thesis" not in row["metadata"] and "rationale" not in row["metadata"]
    full = await tools["evidence_read"](expected_ref)
    assert full["data"]["record"] == store.get(expected_ref)
