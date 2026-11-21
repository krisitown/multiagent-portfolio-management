"""The final lead reserve routes directly to native output without early tool loss."""

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI
from strands.models.openai import OpenAIModel

from tests.thin.test_agents import context
from wheel_team.agents import base
from wheel_team.config import FinalizationConfig
from wheel_team.models import invocation_failure
from wheel_team.types import TeamResult


def mock_model(*steps, on_request=None):
    """Replace only HTTP transport; SDK formatting, hooks and validation remain real."""
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if on_request:
            on_request(len(requests))
        name, arguments = steps[len(requests) - 1]
        chunks = [
            {
                "id": "offline",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "offline",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": f"call-{len(requests)}",
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "offline",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "offline",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
            {
                "id": "offline",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "offline",
                "choices": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        ]
        sse = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=sse + "data: [DONE]\n\n"
        )

    client = AsyncOpenAI(
        api_key="offline",
        base_url="http://offline.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    return OpenAIModel(client=client, model_id="offline"), client, requests


def phase_events(ctx):
    return [
        event
        for line in ctx.trace.path.read_text().splitlines()
        if (event := json.loads(line))["event"] == "lead_synthesis_started"
    ]


def assert_named_output(body):
    assert [entry["function"]["name"] for entry in body["tools"]] == ["TeamResult"]
    assert body["tool_choice"] == {"type": "function", "function": {"name": "TeamResult"}}
    schema = body["tools"][0]["function"]["parameters"]
    assert schema["properties"]["candidate_slots"]["type"] == "array"
    assert "report_markdown" not in schema["properties"]


async def test_time_reserve_preserves_delegation_after_consult_then_routes_exact_boundary(
    tmp_path, monkeypatch
):
    clock = [99.0]
    monkeypatch.setattr(base, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    executed = []

    async def consult_expert(question: str) -> dict:
        """Return a completed synthetic review before the final reserve."""
        executed.append("consult")
        return {"assessment": "Review leaves a research follow-up to the lead"}

    async def research_batch(jobs: list[dict]) -> dict:
        """Complete the lead's delegated follow-up before synthesis."""
        executed.append("batch")
        clock[0] = 100.0
        return {"jobs": jobs, "status": "success"}

    model, client, requests = mock_model(
        ("consult_expert", {"question": "Review saved work"}),
        ("research_batch", {"jobs": [{"job_id": "offline", "question": "Inspect"}]}),
        (
            "TeamResult",
            {"summary": "Research follow-up and review completed", "candidate_slots": []},
        ),
    )
    ctx = context(
        tmp_path,
        "team_lead",
        model,
        {"consult_expert": consult_expert, "research_batch": research_batch},
    )
    ctx.monotonic_deadline = 200.0
    ctx.config = ctx.config.model_copy(
        update={
            "finalization": FinalizationConfig(
                analyst_reserve_seconds=100, expert_reserve_seconds=100
            )
        }
    )
    try:
        response = await base.build_role("team_lead", ctx).invoke_async(
            "Inspect", structured_output_model=TeamResult
        )
    finally:
        await client.close()
    assert executed == ["consult", "batch"]
    for request in requests[:2]:
        assert {entry["function"]["name"] for entry in request["tools"]} == {
            "consult_expert",
            "research_batch",
            "TeamResult",
        }
        assert "tool_choice" not in request
    assert_named_output(requests[2])
    assert response.structured_output.summary == "Research follow-up and review completed"
    assert ctx.terminal_only
    assert len(phase_events(ctx)) == 1
    assert phase_events(ctx)[0]["boundary"] == "time_reserve"


async def test_first_reserved_generation_is_after_last_unreserved_call(tmp_path):
    executed = []

    async def research_batch(jobs: list[dict]) -> dict:
        """Read while one ordinary shared generation remains."""
        executed.append(jobs[0]["job_id"])
        return {"status": "success"}

    model, client, requests = mock_model(
        ("research_batch", {"jobs": [{"job_id": "offline", "question": "Inspect"}]}),
        ("TeamResult", {"summary": "Completed", "candidate_slots": []}),
    )
    ctx = context(tmp_path, "team_lead", model, {"research_batch": research_batch}, limit=6)
    ctx.budget.generations = 3
    ctx.config = ctx.config.model_copy(
        update={
            "max_generations": 6,
            "finalization": FinalizationConfig(
                analyst_reserve_generations=3, expert_reserve_generations=2
            ),
        }
    )
    try:
        response = await base.build_role("team_lead", ctx).invoke_async(
            "Inspect", structured_output_model=TeamResult
        )
    finally:
        await client.close()
    assert executed == ["offline"]
    assert {entry["function"]["name"] for entry in requests[0]["tools"]} == {
        "research_batch",
        "TeamResult",
    }
    assert "tool_choice" not in requests[0]
    assert_named_output(requests[1])
    assert response.structured_output.summary == "Completed"
    assert ctx.budget.generations == 5
    assert len(phase_events(ctx)) == 1
    assert phase_events(ctx)[0]["generation"] == 5
    assert phase_events(ctx)[0]["boundary"] == "generation_reserve"


@pytest.mark.parametrize("repairs", [0, 1])
async def test_synthesis_is_sticky_and_native_invalid_output_keeps_repair_ceiling(
    tmp_path, monkeypatch, repairs
):
    clock = [100.0]
    monkeypatch.setattr(base, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def rewind_after_first_request(number):
        if number == 1:
            clock[0] = 0.0

    async def research_batch(jobs: list[dict]) -> dict:
        """Must not be advertised after entering synthesis."""
        raise AssertionError("A synthesis call cannot delegate more research")

    proposal = {"summary": "Rejected native proposal", "candidate_slots": "[]"}
    model, client, requests = mock_model(
        ("TeamResult", proposal),
        ("TeamResult", {"summary": "A separate corrected proposal", "candidate_slots": []}),
        on_request=rewind_after_first_request,
    )
    ctx = context(tmp_path, "team_lead", model, {"research_batch": research_batch}, limit=6)
    ctx.monotonic_deadline = 200.0
    ctx.config = ctx.config.model_copy(
        update={
            "finalization": FinalizationConfig(
                analyst_reserve_seconds=100, expert_reserve_seconds=100, max_output_repairs=repairs
            )
        }
    )
    try:
        if repairs:
            response = await base.build_role("team_lead", ctx).invoke_async(
                "Inspect", structured_output_model=TeamResult
            )
            assert response.structured_output.summary == "A separate corrected proposal"
            feedback = [
                message for message in requests[1]["messages"] if message["role"] == "tool"
            ][0]
            assert "Input should be a valid tuple" in feedback["content"]
        else:
            with pytest.raises(Exception) as caught:
                await base.build_role("team_lead", ctx).invoke_async(
                    "Inspect", structured_output_model=TeamResult
                )
            assert invocation_failure(caught.value) == "output_repairs"
    finally:
        await client.close()
    assert len(requests) == repairs + 1
    assert ctx.budget.generations == repairs + 1
    for request in requests:
        assert_named_output(request)
    assert len(phase_events(ctx)) == 1
    assert ctx.terminal_only
    events = [json.loads(line) for line in ctx.trace.path.read_text().splitlines()]
    calls = [event for event in events if event["event"] == "tool_call_started"]
    outcomes = [event for event in events if event["event"] == "tool_call_finished"]
    assert calls[0]["inputs"] == proposal
    assert outcomes[0]["status"] == "error" and outcomes[0]["result"] is None
