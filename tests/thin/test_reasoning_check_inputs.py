"""Only selected originals enter a fresh reasoning check; budget the whole request."""

import json

import pytest

from portfolio_management.fact_check import EvidenceSelectionError, TokenCounter, selected_evidence
from portfolio_management.privacy import PrivateEvidenceStore
from portfolio_management.vault import EvidenceStore


def evidence(identity, content):
    return {
        "evidence_id": identity,
        "source": "synthetic-source",
        "delay_state": "unknown",
        "fetched_at": "2026-11-02T10:00:00Z",
        "source_url": "https://example.invalid/" + identity,
        "content": content,
        "units": {"value": "USD"},
    }


def test_unrelated_store_growth_cannot_enlarge_selected_packet(tmp_path):
    store = EvidenceStore(tmp_path / "records")
    ref = store.put("evidence", evidence("selected", {"value": "800"}))
    before = selected_evidence([ref], store)
    for index in range(30):
        store.put("evidence", evidence("unrelated" + str(index), "x" * 40000))
    after = selected_evidence([ref], store)
    assert after == before and len(json.dumps(after)) < 2000


def test_body_dedupe_retains_aliases_and_all_original_provenance(tmp_path):
    store = EvidenceStore(tmp_path / "records")
    a = store.put("evidence", evidence("a", {"value": "800"}))
    b = store.put("evidence", evidence("b", {"value": "800"}))
    packet = selected_evidence([a, b, a], store)
    assert len(packet) == 1 and packet[0]["aliases"] == [a, b]
    for original in packet[0]["provenance"]:
        restored = {**original["metadata"], "content": packet[0]["body"]}
        assert restored == store.get(original["ref"])


def test_public_selection_cannot_read_private_record_or_input(tmp_path):
    public = EvidenceStore(tmp_path / "public")
    private = PrivateEvidenceStore(tmp_path / "private", public)
    ref = private.put("evidence", evidence("private", {"owner": "PRIVATE"}))
    for requested in [ref, "input:portfolio"]:
        with pytest.raises(EvidenceSelectionError):
            selected_evidence([requested], public)
    assert selected_evidence([ref], private)[0]["body"] == {"owner": "PRIVATE"}


def test_explicit_private_input_is_selected_not_automatically_attached(tmp_path):
    store = EvidenceStore(tmp_path / "records")
    inputs = {
        "input:portfolio": {
            "body": "Exact signed holdings with source dates",
            "metadata": {"scope": "private", "source": "original supplied input"},
        }
    }
    assert selected_evidence([], store, inputs) == []
    assert (
        selected_evidence(["input:portfolio"], store, inputs)[0]["body"]
        == "Exact signed holdings with source dates"
    )


def test_batchencoding_count_uses_input_ids_and_includes_output_reserve():
    class Tokenizer:
        chat_template = "verified synthetic chat template"

        def apply_chat_template(self, messages, **kwargs):
            assert messages == [
                {"role": "system", "content": "S"},
                {"role": "user", "content": "U"},
            ]
            assert kwargs == {"tokenize": True, "add_generation_prompt": True}
            return {"input_ids": list(range(82)), "attention_mask": [1] * 82}

    counter = TokenCounter(Tokenizer(), {"model": "synthetic-test", "revision": "explicit-test"})
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    exact = counter.measure(messages, output_tokens=10, context_limit=92)
    assert exact["input_tokens"] == 82 and exact["fits"]
    assert not counter.measure(messages, output_tokens=11, context_limit=92)["fits"]
    assert not exact["is_upper_bound"]


def test_no_tokenizer_fallback_is_explicit_and_counts_system_plus_material():
    counter = TokenCounter()
    small = counter.measure(
        [{"role": "system", "content": "K"}, {"role": "user", "content": "M"}],
        output_tokens=4096,
        context_limit=262144,
    )
    large = counter.measure(
        [{"role": "system", "content": "K" * 300000}, {"role": "user", "content": "M"}],
        output_tokens=4096,
        context_limit=262144,
    )
    assert small["is_upper_bound"] and small["fits"] and not large["fits"]
    assert "upper_bound" in small["method"]


def test_checker_reserve_must_fit_configuration_before_any_run():
    from pydantic import ValidationError

    from portfolio_management.config import FactCheckConfig

    with pytest.raises(ValidationError):
        FactCheckConfig(max_output_tokens=262144, context_window_tokens=262144)
