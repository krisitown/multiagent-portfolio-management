import json
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from portfolio_management.telemetry.observer import PublicOutputObserver


def ctx(tmp_path):
    return SimpleNamespace(
        run_dir=tmp_path, invocation_id="analyst:a:1", budget=SimpleNamespace(generations=7)
    )


def test_only_public_text_and_split_secrets_are_written(tmp_path):
    o = PublicOutputObserver(ctx(tmp_path), "analyst")
    o.before_model(None)
    for text in ["AAPL ", "AAPL ", "API_", "KEY=", "private-", "value ", "safe words"]:
        o(data=text, delta={"text": text})
    o(
        reasoning=True,
        reasoningText="HIDDEN_REASONING",
        delta={"reasoningContent": {"text": "HIDDEN_REASONING"}},
    )
    o(data="HIDDEN_MIXED", delta={"text": "HIDDEN_MIXED", "reasoningContent": {}})
    o(current_tool_use={"input": "TOOL_SECRET"})
    o.after_model(
        SimpleNamespace(
            stop_response=SimpleNamespace(
                message={
                    "content": [
                        {"text": "AAPL AAPL API_KEY=private-value safe words"},
                        {"reasoningContent": {"text": "HIDDEN_REASONING"}},
                    ]
                }
            )
        )
    )
    o.close(wait=1)
    data = (tmp_path / "public-output.jsonl").read_text()
    assert "HIDDEN" not in data
    assert "TOOL_SECRET" not in data
    assert "private-value" not in data
    rows = [json.loads(x) for x in data.splitlines()]
    final = next(e for e in rows if e.get("final"))
    assert final["text"] == "AAPL AAPL API_KEY=[redacted] safe words"
    assert final["generation"] == 7


def test_slow_io_is_bounded_and_never_blocks_callback(tmp_path):
    o = PublicOutputObserver(ctx(tmp_path), "analyst", queue_size=2)
    entered = threading.Event()
    release = threading.Event()

    def blocked(row):
        entered.set()
        release.wait(3)

    o._write = blocked
    o.before_model(None)
    o(data="one ", delta={"text": "one "})
    assert entered.wait(1)
    started = time.monotonic()
    for _ in range(500):
        o(data="two ", delta={"text": "two "})
    o.close()
    assert time.monotonic() - started < 0.2
    assert o.dropped_events > 0
    assert o._queue.qsize() <= 2
    release.set()
    o.close(wait=1)


def test_broken_io_is_counted_without_raising(tmp_path):
    (tmp_path / "public-output.jsonl").mkdir()
    o = PublicOutputObserver(ctx(tmp_path), "analyst")
    o.before_model(None)
    o(data="safe ", delta={"text": "safe "})
    o.close(wait=1)
    assert o.io_failures > 0


@pytest.mark.asyncio
async def test_scripted_agent_return_identical_enabled_disabled_and_failed(tmp_path, monkeypatch):
    import asyncio

    from portfolio_management.agents.base import build_role
    from portfolio_management.config import TeamConfig
    from portfolio_management.contracts import AgentRole
    from portfolio_management.models import ScriptedModel
    from portfolio_management.prompt_loader import load_prompt
    from portfolio_management.types import RoleContext, RunBudget, TeamRequest, TraceSink

    results = []
    for variant in ("off", "on", "failed"):
        run = tmp_path / variant
        run.mkdir()
        if variant == "failed":
            (run / "public-output.jsonl").mkdir()
        monkeypatch.setenv("WHEEL_TEAM_PUBLIC_OUTPUT", "0" if variant == "off" else "1")
        config = TeamConfig(
            model={"model_id": "offline"},
            prompts={"mode": "development"},
            child_output_mode="direct_text",
        )
        budget = RunBudget(10, 2, datetime.now(UTC) + timedelta(seconds=60), asyncio.Semaphore(1))
        context = RoleContext(
            request=TeamRequest(question="Review AAPL", mode="focused"),
            config=config,
            model=ScriptedModel(config.model, steps=[{"text": "Exact AAPL\n output  "}]),
            prompt=load_prompt(AgentRole.ANALYST, config.prompts),
            tools={},
            budget=budget,
            trace=TraceSink(run / "trace.jsonl", lambda: datetime.now(UTC)),
            run_dir=run,
            invocation_id="analyst:a:1",
        )
        agent = build_role("analyst", context)
        result = await agent.invoke_async("Review AAPL")
        results.append({k: v for k, v in result.message.items() if k != "tracking_id"})
    assert results[0] == results[1] == results[2]
    for _ in range(100):
        p = tmp_path / "on/public-output.jsonl"
        if p.exists() and '"final": true' in p.read_text():
            break
        await asyncio.sleep(0.01)
    assert "Exact AAPL" in p.read_text()


def test_measured_handoff_size_and_reused_agent_observer(tmp_path):
    o = PublicOutputObserver(ctx(tmp_path), "analyst")
    for generation in (7, 8):
        o.context.budget.generations = generation
        o.before_invocation(
            SimpleNamespace(messages=[{"role": "user", "content": [{"text": "Review AAPL"}]}])
        )
        o.before_model(None)
        o(data="AAPL ", delta={"text": "AAPL "})
        o.after_model(
            SimpleNamespace(stop_response=SimpleNamespace(message={"content": [{"text": "AAPL"}]}))
        )
        o.close(wait=1)
    rows = [json.loads(x) for x in (tmp_path / "public-output.jsonl").read_text().splitlines()]
    assert [e["prompt_chars"] for e in rows if e["event"] == "invocation_started"] == [11, 11]
    assert [e["generation"] for e in rows if e.get("final")] == [7, 8]
    assert [
        e["generation"]
        for e in rows
        if e["event"] == "public_prompt" and e["prompt_kind"] == "user"
    ] == [None, None]


@pytest.mark.asyncio
async def test_public_text_is_readable_before_model_returns(tmp_path, monkeypatch):
    import asyncio

    from tests.thin.test_agents import context
    from portfolio_management.agents.base import build_role
    from portfolio_management.models import ScriptedModel
    from portfolio_management.telemetry.reader import RunReader
    from portfolio_management.telemetry.state import RunState

    release = asyncio.Event()
    monkeypatch.setenv("WHEEL_TEAM_PUBLIC_OUTPUT", "1")
    cfg = context(tmp_path, "analyst", None).config

    class PausedModel(ScriptedModel):
        async def stream(self, *args, **kwargs):
            yield {"messageStart": {"role": "assistant"}}
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "AAPL public interim "}}}
            await release.wait()
            yield {"contentBlockDelta": {"delta": {"reasoningContent": {"text": "NEVER_OBSERVE"}}}}
            yield {"contentBlockDelta": {"delta": {"text": "final"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}

    agent = build_role("analyst", context(tmp_path, "analyst", PausedModel(cfg.model)))
    task = asyncio.create_task(agent.invoke_async("Review AAPL"))
    reader, state, cursor = RunReader(tmp_path), RunState(), None
    try:
        for _ in range(100):
            result = reader.poll(cursor)
            cursor = result["cursor"]
            state.apply(result["events"])
            if any(
                g.get("text") == "AAPL public interim "
                for i in state.snapshot()["invocations"]
                for g in i["generations"]
            ):
                break
            await asyncio.sleep(0.01)
        assert not task.done()
        assert any(
            g.get("text") == "AAPL public interim "
            for i in state.snapshot()["invocations"]
            for g in i["generations"]
        )
    finally:
        release.set()
        result = await task
    assert (
        "".join(b.get("text", "") for b in result.message["content"]) == "AAPL public interim final"
    )
    assert "NEVER_OBSERVE" not in (tmp_path / "public-output.jsonl").read_text()


def test_fenced_and_quoted_credentials_are_redacted(tmp_path):
    from portfolio_management.telemetry.privacy import clean

    for text in [
        'Credentials:\n```json\n{"api_key": "ordinary-secret-value"}\n```',
        'password = "private value with spaces"',
        "authorization: Basic cHJpdmF0ZS12YWx1ZQ==",
    ]:
        safe = clean(text)
        assert "ordinary-secret-value" not in safe
        assert "private value" not in safe
        assert "cHJpdmF0ZS12YWx1ZQ==" not in safe


def test_reused_invocation_does_not_multiply_blocked_writer_threads(tmp_path):
    o = PublicOutputObserver(ctx(tmp_path), "analyst", queue_size=2)
    entered, release = threading.Event(), threading.Event()

    def blocked(row):
        entered.set()
        release.wait(3)

    o._write = blocked
    original_worker = o._worker
    o.before_model(None)
    o(data="AAPL ", delta={"text": "AAPL "})
    assert entered.wait(1)
    try:
        for _ in range(20):
            o.close()
            o.before_invocation(SimpleNamespace(messages=[]))
            o.before_model(None)
            o(data="AMD ", delta={"text": "AMD "})
        assert o._worker is original_worker
        assert o.dropped_events > 0
    finally:
        release.set()
        o.close(wait=1)


def test_received_prompts_are_public_text_only_redacted_and_bounded(tmp_path):
    from portfolio_management.telemetry.observer import PROMPT_LIMIT

    o = PublicOutputObserver(ctx(tmp_path), "analyst")
    o.before_invocation(
        SimpleNamespace(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"text": "Actual task API_KEY=private-value"},
                        {"reasoningContent": {"text": "NEVER_HIDDEN"}},
                        {"text": "NEVER_MIXED", "reasoningContent": {}},
                    ],
                }
            ]
        )
    )
    o.before_model(
        SimpleNamespace(
            agent=SimpleNamespace(system_prompt='System instructions password="never-secret"')
        )
    )
    o.before_model(
        SimpleNamespace(
            agent=SimpleNamespace(system_prompt='System instructions password="never-secret"')
        )
    )
    o.close(wait=1)
    rows = [json.loads(x) for x in (tmp_path / "public-output.jsonl").read_text().splitlines()]
    prompts = [r for r in rows if r["event"] == "public_prompt"]
    assert [p["prompt_kind"] for p in prompts] == ["user", "system"]
    assert prompts[0]["text"] == "Actual task API_KEY=[redacted]"
    assert "never-secret" not in json.dumps(prompts)
    assert "NEVER_" not in json.dumps(prompts)
    assert prompts[1]["generation"] == 7
    assert all(p["prompt_id"] for p in prompts)

    o = PublicOutputObserver(ctx(tmp_path), "analyst")
    o.before_invocation(
        SimpleNamespace(
            messages=[{"role": "user", "content": [{"text": "A" * (PROMPT_LIMIT + 100)}]}]
        )
    )
    o.close(wait=1)
    rows = [json.loads(x) for x in (tmp_path / "public-output.jsonl").read_text().splitlines()]
    large = [r for r in rows if r["event"] == "public_prompt"][-1]
    assert len(large["text"]) <= PROMPT_LIMIT
    assert large["truncated"] is True
