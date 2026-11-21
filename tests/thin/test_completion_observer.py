"""Native provider events expose safe counters without retaining response bodies."""

import json
from types import SimpleNamespace

from openai.types import CompletionUsage
from openai.types.chat.chat_completion_chunk import ChoiceDeltaToolCall


def test_unobserved_provider_values_are_unknown_not_zero():
    from wheel_team.model_observer import CompletionTelemetry

    snapshot = CompletionTelemetry().snapshot()
    assert snapshot["observed"] is False
    assert snapshot["finish_reason"] is None
    assert snapshot["usage"] is None
    assert snapshot["visible_text_chars"] is None
    assert snapshot["tool_argument_chars"] is None


def test_native_finish_usage_and_reasoning_token_count_survive_sdk_normalization():
    from wheel_team.model_observer import CompletionTelemetry, ObservedOpenAIModel

    telemetry = CompletionTelemetry()
    model = ObservedOpenAIModel(telemetry=telemetry, model_id="offline-unit")
    usage = CompletionUsage(
        prompt_tokens=101,
        completion_tokens=19,
        total_tokens=120,
        completion_tokens_details={"reasoning_tokens": 7, "audio_tokens": 0},
        prompt_tokens_details={"cached_tokens": 3},
    )
    assert model.format_chunk({"chunk_type": "message_stop", "data": "length"}) == {
        "messageStop": {"stopReason": "max_tokens"}
    }
    formatted = model.format_chunk({"chunk_type": "metadata", "data": usage})
    assert formatted["metadata"]["usage"]["outputTokens"] == 19
    snapshot = telemetry.snapshot()
    assert snapshot["finish_reason"] == "length"
    assert snapshot["usage"] == {
        "prompt_tokens": 101,
        "completion_tokens": 19,
        "total_tokens": 120,
        "completion_tokens_details": {"reasoning_tokens": 7, "audio_tokens": 0},
        "prompt_tokens_details": {"cached_tokens": 3},
    }


def test_text_tool_arguments_and_hidden_reasoning_bodies_are_never_serialized():
    from wheel_team.model_observer import CompletionTelemetry, ObservedOpenAIModel

    telemetry = CompletionTelemetry()
    model = ObservedOpenAIModel(telemetry=telemetry, model_id="offline-unit")
    for event in [
        {"chunk_type": "content_delta", "data_type": "text", "data": "VISIBLE_MARKER"},
        {
            "chunk_type": "content_delta",
            "data_type": "reasoning_content",
            "data": "HIDDEN_SECRET_MARKER",
        },
        {
            "chunk_type": "content_start",
            "data_type": "tool",
            "data": ChoiceDeltaToolCall(
                index=0, id="unit", function={"name": "ResearchResult", "arguments": ""}
            ),
        },
        {
            "chunk_type": "content_delta",
            "data_type": "tool",
            "data": ChoiceDeltaToolCall(index=0, function={"arguments": "ARG_SECRET"}),
        },
        {
            "chunk_type": "content_delta",
            "data_type": "tool",
            "data": ChoiceDeltaToolCall(index=0, function={"arguments": "{}"}),
        },
    ]:
        model.format_chunk(event)
    snapshot = telemetry.snapshot()
    assert snapshot["visible_text_chars"] == 14
    assert snapshot["tool_argument_chars"] == {"ResearchResult": 12}
    encoded = json.dumps(snapshot)
    for secret in ["VISIBLE_MARKER", "HIDDEN_SECRET_MARKER", "ARG_SECRET"]:
        assert secret not in encoded
        assert secret not in repr(telemetry.__dict__)


def test_unknown_provider_fields_and_non_numeric_token_details_are_ignored():
    from wheel_team.model_observer import CompletionTelemetry

    telemetry = CompletionTelemetry()
    telemetry.observe(
        {
            "chunk_type": "metadata",
            "data": SimpleNamespace(
                prompt_tokens=1,
                completion_tokens=2,
                total_tokens=3,
                completion_tokens_details=SimpleNamespace(
                    reasoning_tokens="PRIVATE_SECRET", hidden=99
                ),
                prompt_tokens_details=SimpleNamespace(cached_tokens=True),
                private="PRIVATE_SECRET",
            ),
        }
    )
    telemetry.observe({"chunk_type": "message_stop", "data": "PRIVATE_SECRET"})
    snapshot = telemetry.snapshot()
    assert snapshot["finish_reason"] is None
    assert snapshot["usage"] == {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
    assert "PRIVATE_SECRET" not in json.dumps(snapshot)


def test_formatted_input_hash_excludes_request_controls_and_does_not_store_bodies():
    from wheel_team.model_observer import CompletionTelemetry, ObservedOpenAIModel

    snapshots = []
    for controls in [
        {"max_tokens": 8192, "extra_body": {"include_reasoning": False}},
        {
            "max_tokens": 8192,
            "reasoning_effort": "high",
            "extra_body": {"include_reasoning": False},
        },
        {
            "max_tokens": 8192,
            "extra_body": {
                "include_reasoning": False,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        },
    ]:
        telemetry = CompletionTelemetry()
        model = ObservedOpenAIModel(telemetry=telemetry, model_id="offline-unit", params=controls)
        request = model.format_request([{"role": "user", "content": [{"text": "INPUT_SECRET"}]}])
        assert request["messages"][0]["content"][0]["text"] == "INPUT_SECRET"
        snapshots.append(telemetry.snapshot())
    assert len({snapshot["formatted_input_sha256"] for snapshot in snapshots}) == 1
    assert snapshots[1]["request_controls"]["reasoning_effort"] == "high"
    assert snapshots[2]["request_controls"]["extra_body"]["chat_template_kwargs"] == {
        "enable_thinking": False
    }
    assert all(snapshot["observed"] is False for snapshot in snapshots)
    assert "INPUT_SECRET" not in json.dumps(snapshots)
