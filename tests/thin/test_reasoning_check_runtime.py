"""Explicit checker execution limits preserve ordinary feedback without verdict parsing."""

import asyncio
import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_direct_text_handoff import CutoffModel, direct_config
from wheel_team.fact_check import TokenCounter
from wheel_team.fact_check_runtime import ReasoningCheckSession
from wheel_team.prompt_loader import _CheckerPromptBundle
from wheel_team.types import RoleContext, RunBudget, TeamRequest, TraceSink
from wheel_team.vault import EvidenceStore


def session(tmp_path, models, **kwargs):
    cfg = direct_config(tmp_path)
    budget = RunBudget(20, 1, datetime.now(UTC) + timedelta(seconds=30), asyncio.Semaphore(1))
    trace = TraceSink(tmp_path / "trace.jsonl", lambda: datetime.now(UTC))
    context = RoleContext(
        request=TeamRequest(question="PRIVATE OWNER REQUEST", mode="education"),
        config=cfg,
        model=None,
        prompt=_CheckerPromptBundle(
            prompt_id="test",
            version="1",
            content="General mechanics",
            content_hash="a" * 64,
            status="development",
        ),
        tools={},
        budget=budget,
        trace=trace,
        run_dir=tmp_path,
        store=EvidenceStore(tmp_path / "records"),
        invocation_id="analyst:a",
        monotonic_deadline=time.monotonic() + 30,
    )
    created = []

    def factory():
        created.append(True)
        return models.pop(0)

    checker = ReasoningCheckSession(
        tmp_path / "checks",
        context,
        factory,
        author_role="analyst",
        counter=TokenCounter(),
        **kwargs,
    )
    return checker, context, created


async def test_every_explicit_call_is_fresh_zero_tool_and_feedback_is_unparsed(tmp_path):
    replies = ["Conditional: if rates stay flat, 2×4×100 = 800.", "{not a verdict schema}"]
    models = [ScriptModel({"text": reply}) for reply in replies]
    original = models.copy()
    checker, context, created = session(tmp_path, models)
    for reply in replies:
        result = await checker.check("Exact question", [], ["Rates stay flat"])
        assert result["status"] == "completed" and result["feedback"] == reply
        assert "passed" not in result
    assert len(created) == context.budget.generations == 2
    for model in original:
        assert not model.calls[0]["tools"]
        assert len(model.calls[0]["messages"]) == 1
        packet = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
        assert packet["message"] == "Exact question"
        assert packet["assumptions"] == ["Rates stay flat"]
        assert packet["evidence"] == [] and "PRIVATE" not in json.dumps(packet)
    assert len(list((tmp_path / "checks").glob("call-*.json"))) == 2


async def test_oversize_and_unauthorized_selection_never_construct_model(tmp_path):
    checker, _, created = session(tmp_path, [])
    large = await checker.check("X" * 300000, [], [])
    assert large["status"] == "oversize" and not large["budget"]["fits"]
    denied = await checker.check("Check", ["input:portfolio"], [])
    assert denied["status"] == "error" and "unauthorized" in denied["error"]
    assert not created


@pytest.mark.parametrize(
    "model,category,body",
    [
        (
            CutoffModel({"text": "Partial ordinary reply"}),
            "stop_reason:max_tokens",
            "Partial ordinary reply",
        ),
        (ScriptModel(output("fact_check", message="recursive")), "forbidden_tool_use", ""),
        (ScriptModel(RuntimeError("secret-token-not-for-log")), "RuntimeError", ""),
        (ScriptModel({"text": ""}), "empty_reply", ""),
    ],
)
async def test_operational_errors_retain_partial_reply_and_safe_precise_reason(
    tmp_path, model, category, body
):
    checker, _, _ = session(tmp_path, [model])
    result = await checker.check("Question", [], [])
    assert result["status"] == "error" and category in result["error"]
    saved = json.loads((tmp_path / "checks/call-1.json").read_text())
    assert saved["result"]["feedback"] == body
    assert "secret-token-not-for-log" not in json.dumps(saved)


async def test_three_calls_are_durable_no_cache_no_retry(tmp_path):
    checker, context, created = session(
        tmp_path, [ScriptModel({"text": "Reply"}) for _ in range(3)]
    )
    for _ in range(3):
        assert (await checker.check("Identical question", [], []))["status"] == "completed"
    assert (await checker.check("Fourth", [], []))["status"] == "limit_reached"
    restarted = ReasoningCheckSession(
        tmp_path / "checks", context, lambda: None, author_role="analyst", counter=TokenCounter()
    )
    assert (await restarted.check("Again", [], []))["status"] == "limit_reached"
    assert len(created) == context.budget.generations == 3


async def test_deadline_generation_and_held_analyst_permit_are_shared(tmp_path):
    checker, context, _ = session(tmp_path, [ScriptModel({"text": "Reply"})])
    async with context.budget.analyst_semaphore:
        assert (await asyncio.wait_for(checker.check("Question", [], []), 2))[
            "status"
        ] == "completed"
    context.budget.generations = context.budget.max_generations
    assert "generation" in (await checker.check("Again", [], []))["error"].lower()
    checker.context = replace(context, invocation_deadline=time.monotonic() - 1)
    assert "deadline" in (await checker.check("Late", [], []))["error"].lower()


async def test_cancelled_call_is_reserved_and_saved(tmp_path):
    checker, context, _ = session(tmp_path, [ScriptModel(asyncio.Event())])
    task = asyncio.create_task(checker.check("Question", [], []))
    while context.budget.generations == 0:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = json.loads((tmp_path / "checks/call-1.json").read_text())
    assert saved["result"]["error"] == "cancelled"
    assert checker.calls == 1


async def test_transport_failure_retains_streamed_ordinary_text_only(tmp_path):
    class BrokenStream(ScriptModel):
        async def stream(self, *args, **kwargs):
            yield {"messageStart": {"role": "assistant"}}
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"reasoningContent": {"text": "HIDDEN"}}}}
            yield {"contentBlockStop": {}}
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "Partial ordinary feedback"}}}
            raise RuntimeError("transport-secret")

    checker, _, _ = session(tmp_path, [BrokenStream()])
    result = await checker.check("Question", [], [])
    assert result["status"] == "error" and result["feedback"] == "Partial ordinary feedback"
    saved = (tmp_path / "checks/call-1.json").read_text()
    assert "HIDDEN" not in saved and "transport-secret" not in saved
