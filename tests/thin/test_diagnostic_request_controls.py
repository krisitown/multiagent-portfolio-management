"""Verify opt-in Qwen controls on the actual OpenAI-compatible wire, offline."""

import json

import httpx
import pytest
from pydantic import ValidationError

from wheel_team import models
from wheel_team.config import ModelConfig
from wheel_team.model_observer import CompletionTelemetry


@pytest.mark.parametrize("effort", ["minimal", "max"])
def test_unverified_effort_values_are_rejected(effort):
    with pytest.raises(ValidationError):
        ModelConfig(model_id="offline", reasoning_effort=effort)


@pytest.mark.parametrize(
    "controls,expected",
    [
        ({}, {}),
        ({"reasoning_effort": "high"}, {"reasoning_effort": "high"}),
        ({"include_reasoning": False}, {"include_reasoning": False}),
        (
            {"include_reasoning": False, "reasoning_effort": "low"},
            {"include_reasoning": False, "reasoning_effort": "low"},
        ),
        (
            {"include_reasoning": False, "enable_thinking": False},
            {"include_reasoning": False, "chat_template_kwargs": {"enable_thinking": False}},
        ),
    ],
)
async def test_request_controls_reach_wire_without_overriding_cap_or_defaults(
    monkeypatch, controls, expected
):
    requests = []
    original = httpx.AsyncClient

    def handle(request):
        requests.append(json.loads(request.content))
        assert request.extensions["timeout"]["read"] == 3600
        chunks = [
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "visible"},
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 17,
                    "total_tokens": 28,
                    "completion_tokens_details": {"reasoning_tokens": 12},
                },
            },
        ]
        data = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
        return httpx.Response(
            200, content=data + "data: [DONE]\n\n", headers={"Content-Type": "text/event-stream"}
        )

    class OfflineClient(original):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(models.httpx, "AsyncClient", OfflineClient)
    monkeypatch.setenv("WIRE_TEST_API_KEY", "offline-nonsecret")
    config = ModelConfig(
        provider="openai",
        model_id="qwen",
        endpoint="https://offline.invalid/v1",
        api_key_env="WIRE_TEST_API_KEY",
        allow_inference_network=True,
        max_output_tokens=65536,
        timeout_seconds=3600,
        **controls,
    )
    telemetry = CompletionTelemetry()
    model, client = models.build_model(config, telemetry=telemetry)
    try:
        events = [
            event
            async for event in model.stream(
                [{"role": "user", "content": [{"text": "same frozen input"}]}],
                system_prompt="same approved prefix",
            )
        ]
    finally:
        await client.close()
    assert events and len(requests) == 1
    native = telemetry.snapshot()
    assert native["finish_reason"] == "stop"
    assert native["usage"]["completion_tokens_details"]["reasoning_tokens"] == 12
    assert native["visible_text_chars"] == 7
    assert native["formatted_input_sha256"]
    wire = requests[0]
    assert wire["max_tokens"] == 65536
    assert "max_completion_tokens" not in wire
    assert "extra_body" not in wire  # OpenAI merges it into the wire JSON.
    for key in ("reasoning_effort", "include_reasoning", "chat_template_kwargs"):
        if key in expected:
            assert wire[key] == expected[key]
        else:
            assert key not in wire


@pytest.mark.parametrize("role", ["team_lead", "analyst", "wheel_expert"])
async def test_overcontext_preserves_history_and_surfaces_without_retry(
    tmp_path, monkeypatch, role
):
    # Catches automatic SlidingWindow retry/trimming, incorrect fixed wire ceiling/read
    # timeout, or converting a server context error to successful/incomplete assistant text.
    from strands.types.exceptions import ContextWindowOverflowException

    from tests.thin.test_agents import context
    from wheel_team.agents.base import build_role

    requests = []
    original = httpx.AsyncClient

    def handle(request):
        requests.append(json.loads(request.content))
        assert request.extensions["timeout"]["read"] == 3600
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Input length and `max_tokens` exceed context limit",
                    "type": "invalid_request_error",
                    "code": "context_length_exceeded",
                }
            },
        )

    class OfflineClient(original):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(models.httpx, "AsyncClient", OfflineClient)
    monkeypatch.setenv("WIRE_TEST_API_KEY", "offline-nonsecret")
    config = ModelConfig(
        provider="openai",
        model_id="offline",
        endpoint="https://offline.invalid/v1",
        api_key_env="WIRE_TEST_API_KEY",
        allow_inference_network=True,
        timeout_seconds=3600,
        max_output_tokens=65536,
    )
    model, client = models.build_model(config)
    agent = build_role(role, context(tmp_path, role, model))
    history = [
        {"role": "user", "content": [{"text": "First source " + "α" * 1000}]},
        {"role": "assistant", "content": [{"text": "Original response"}]},
        {"role": "user", "content": [{"text": "Second source " + "β" * 1000}]},
        {"role": "assistant", "content": [{"text": "Original next response"}]},
    ]
    agent.messages.extend(history)
    try:
        with pytest.raises(ContextWindowOverflowException, match="context limit"):
            await agent.invoke_async("Exact latest question")
    finally:
        await client.close()
    assert len(requests) == 1
    assert requests[0]["max_tokens"] == 65536
    assert agent.messages[:4] == history
    assert requests[0]["messages"][1:] == [
        {"role": "user", "content": [{"type": "text", "text": history[0]["content"][0]["text"]}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Original response"}]},
        {"role": "user", "content": [{"type": "text", "text": history[2]["content"][0]["text"]}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Original next response"}]},
        {"role": "user", "content": [{"type": "text", "text": "Exact latest question"}]},
    ]
