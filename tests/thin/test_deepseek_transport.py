"""DeepSeek opaque transport roundtrips without entering native transcripts."""

import json
from types import SimpleNamespace

import pytest
from openai.types.chat import ChatCompletion
from strands import Agent, tool

from wheel_team.models import DeepSeekOpenAIModel


def response(index, *, call=False):
    message = {
        "role": "assistant",
        "content": "same public text",
        "reasoning_content": f"opaque-{index}",
    }
    if call:
        message["tool_calls"] = [
            {
                "id": f"call-{index}",
                "type": "function",
                "function": {"name": "echo", "arguments": '{"value":"ok"}'},
            }
        ]
    return ChatCompletion.model_validate(
        {
            "id": str(index),
            "created": 0,
            "model": "deepseek-flash",
            "object": "chat.completion",
            "choices": [
                {"index": 0, "finish_reason": "tool_calls" if call else "stop", "message": message}
            ],
        }
    )


@pytest.mark.asyncio
async def test_multiple_tool_turns_keep_exact_opaque_association_out_of_native_history():
    requests = []

    async def create(**request):
        requests.append(request)
        return response(len(requests), call=len(requests) < 3)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    model = DeepSeekOpenAIModel(client=client, model_id="deepseek-flash")

    @tool
    def echo(value: str) -> str:
        """Return a public tool value."""
        return value

    agent = Agent(model=model, tools=[echo], callback_handler=None)
    await agent.invoke_async("Use echo twice, then answer.")
    assert len(requests) == 3
    assistants = [m for m in requests[2]["messages"] if m["role"] == "assistant"]
    assert all(isinstance(m.get("content"), (str, type(None))) for m in assistants)
    assert [m["reasoning_content"] for m in assistants] == ["opaque-1", "opaque-2"]
    assert [m["tool_calls"][0]["id"] for m in assistants] == ["call-1", "call-2"]
    encoded = json.dumps(agent.messages)
    assert "opaque-" not in encoded
    assert "reasoningContent" not in encoded
    assert "reasoning_content" not in encoded
    assert all(r["stream"] is False and "stream_options" not in r for r in requests)
    # Separate concurrent invocation instances never share opaque state.
    other = DeepSeekOpenAIModel(client=client, model_id="deepseek-flash")
    assert not other._opaque_turns
