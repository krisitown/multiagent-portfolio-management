"""Offline SDK serialization against isolated deployed tool-validator semantics."""

import hashlib
import json

import httpx
import pytest
from strands.models.openai import OpenAIModel

from tests.thin.test_agents import context
from tests.thin.test_direct_text_handoff import direct_config
from portfolio_management import models
from portfolio_management.agents.base import build_role
from portfolio_management.config import FinalizationConfig, ModelConfig
from portfolio_management.model_observer import CompletionTelemetry


@pytest.fixture
def isolated_deployed_tools_validator():
    """Relevant check_tool_usage branches, NOT a server or full vLLM validator.

    Source: run6/terminal-protocol-diagnosis.json, deployed protocol.py line 901,
    SHA256 cb756e3d18e9061a2b306f305e10bd71d43f01ad1b236d0e9cbbb8756cd504dc.
    These are the empty-array and missing-tools choice branches only.
    """

    def validate(data):
        if data.get("tools") == []:
            raise ValueError(
                "`tools` must not be an empty array. "
                "Either provide at least one tool or omit the field entirely."
            )
        if "tool_choice" not in data and data.get("tools"):
            data["tool_choice"] = "auto"
        if data.get("tool_choice") == "none":
            return data
        if "tool_choice" in data and data["tool_choice"] is not None:
            if "tools" not in data or data["tools"] is None:
                raise ValueError("When using `tool_choice`, `tools` must be set.")
        return data

    return validate


def test_installed_sdk_empty_tools_reproduces_isolated_deployed_rejection(
    isolated_deployed_tools_validator,
):
    # Real installed formatter; no client construction, server, or inference.
    model = OpenAIModel.__new__(OpenAIModel)
    model.config = {"model_id": "offline", "params": {"max_tokens": 65536}}
    request = model.format_request([], [], tool_choice=None)
    assert request["tools"] == [] and request["max_tokens"] == 65536
    assert "tool_choice" not in request
    with pytest.raises(ValueError, match="must not be an empty array"):
        isolated_deployed_tools_validator(request)


@pytest.fixture(params=[False, True], ids=["ordinary", "completion-telemetry"])
async def inference_model(request, monkeypatch, isolated_deployed_tools_validator):
    requests = []
    original = httpx.AsyncClient

    def respond(http_request):
        body = json.loads(http_request.content)
        # This is an isolated contract fixture, not an actual vLLM HTTP response.
        isolated_deployed_tools_validator(body.copy())
        requests.append(body)
        chunks = [
            {"choices": [{"index": 0, "delta": {"content": "  Final α\n"}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        sse = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=sse + "data: [DONE]\n\n"
        )

    class OfflineClient(original):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(models.httpx, "AsyncClient", OfflineClient)
    monkeypatch.setenv("TOOLS_TEST_KEY", "offline")
    telemetry = CompletionTelemetry() if request.param else None
    model, client = models.build_model(
        ModelConfig(
            provider="openai",
            model_id="offline",
            endpoint="https://offline.invalid/v1",
            api_key_env="TOOLS_TEST_KEY",
            allow_inference_network=True,
            max_output_tokens=65536,
        ),
        telemetry=telemetry,
    )
    try:
        yield model, requests, telemetry
    finally:
        await client.close()


@pytest.mark.parametrize("specs", [None, []])
async def test_no_tools_formatter_omits_field_and_keeps_cap(
    inference_model, isolated_deployed_tools_validator, specs
):
    model, _, _ = inference_model
    request = model.format_request([], specs, tool_choice=None)
    isolated_deployed_tools_validator(request.copy())
    assert "tools" not in request and "tool_choice" not in request
    assert request["max_tokens"] == 65536


@pytest.mark.parametrize(
    "choice,expected",
    [
        (None, None),
        ({"auto": {}}, "auto"),
        ({"any": {}}, "required"),
        ({"tool": {"name": "read"}}, {"type": "function", "function": {"name": "read"}}),
    ],
)
async def test_enabled_tools_and_explicit_choices_are_preserved(inference_model, choice, expected):
    model, _, _ = inference_model
    request = model.format_request(
        [],
        [{"name": "read", "description": "Read", "inputSchema": {"json": {"type": "object"}}}],
        tool_choice=choice,
    )
    assert request["tools"] == [
        {
            "type": "function",
            "function": {"name": "read", "description": "Read", "parameters": {"type": "object"}},
        }
    ]
    if expected is None:
        assert "tool_choice" not in request
    else:
        assert request["tool_choice"] == expected


async def test_no_tools_does_not_silently_rewrite_explicit_choice(
    inference_model, isolated_deployed_tools_validator
):
    model, _, _ = inference_model
    request = model.format_request([], [], tool_choice={"any": {}})
    assert request["tool_choice"] == "required"
    with pytest.raises(ValueError, match="`tools` must be set"):
        isolated_deployed_tools_validator(request)
    # The SDK also accepts OpenAI-native params; explicit none is valid without tools.
    model.update_config(params={"max_tokens": 65536, "tool_choice": "none"})
    request = model.format_request([], [])
    assert request["tool_choice"] == "none" and "tools" not in request
    isolated_deployed_tools_validator(request)


async def test_reserved_direct_text_reaches_mock_http_without_tools(tmp_path, inference_model):
    model, requests, telemetry = inference_model

    async def research_batch(jobs: list[dict]) -> dict:
        """Must be suppressed in the final reserve."""
        raise AssertionError("Research cannot run in the reserve")

    ctx = context(tmp_path, "team_lead", model, {"research_batch": research_batch}, limit=4)
    ctx.config = direct_config(tmp_path).model_copy(
        update={"finalization": FinalizationConfig(expert_reserve_generations=4)}
    )
    response = await build_role("team_lead", ctx).invoke_async("Inspect")
    assert ctx.terminal_only and response.stop_reason == "end_turn"
    assert response.message["content"] == [{"text": "  Final α\n"}]
    assert len(requests) == 1
    wire = requests[0]
    assert "tools" not in wire and "tool_choice" not in wire
    assert wire["max_tokens"] == 65536
    if telemetry is not None:
        selected = {"messages": wire["messages"]}
        assert telemetry.snapshot()["formatted_input_sha256"] == hashlib.sha256(
            json.dumps(selected, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
