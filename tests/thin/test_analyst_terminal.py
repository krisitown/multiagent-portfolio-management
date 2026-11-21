"""Offline SDK lifecycle checks; scripts assert protocol, never investment judgment."""

import asyncio
import hashlib
import json
import os
from collections import deque
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_finalization_team import bounded_config
from wheel_team.team import run_team
from wheel_team.types import TeamDependencies, TeamRequest


def terminal_config(tmp_path, **values):
    defaults = dict(
        analyst_concurrency=1,
        deadline_seconds=540,
        max_generations=24,
        finalization=dict(
            analyst_reserve_seconds=180,
            analyst_reserve_generations=6,
            analyst_terminal_seconds=150,
        ),
    )
    defaults.update(values)
    return bounded_config(tmp_path, **defaults)


@pytest.mark.parametrize("value", [True, "150", -1, float("inf"), float("nan"), 360, 361])
def test_terminal_allowance_is_strict_and_inside_analyst_window(tmp_path, value):
    with pytest.raises(ValidationError):
        terminal_config(
            tmp_path,
            finalization=dict(
                analyst_reserve_seconds=180,
                analyst_terminal_seconds=value,
            ),
        )


def test_terminal_defaults_disabled_and_requires_single_analyst(tmp_path):
    assert bounded_config(tmp_path).finalization.analyst_terminal_seconds == 0
    with pytest.raises(ValidationError, match="concurrency"):
        terminal_config(tmp_path, analyst_concurrency=2)


def saved(result):
    return json.loads(Path(result.artifacts["research"]).read_text())


def transitions(result):
    return [
        row
        for row in map(json.loads, Path(result.artifacts["trace"]).read_text().splitlines())
        if row["event"] == "analyst_terminal_started"
    ]


async def run_models(tmp_path, analyst, *, cfg=None, tools=None, jobs=None):
    lead = ScriptModel(
        output("research_batch", jobs=jobs or [{"job_id": "a", "question": "Inspect"}]),
        output("consult_expert", question="Inspect limitations"),
        output("TeamResult", summary="Mechanical protocol check only"),
    )
    expert = ScriptModel(output("ExpertResult", assessment="Mechanical protocol check only"))
    queue = deque([lead, analyst, expert])
    result = await run_team(
        TeamRequest(question="Offline lifecycle check", mode="education"),
        config=cfg or terminal_config(tmp_path),
        deps=TeamDependencies(
            model_factory=lambda *_: queue.popleft(), tool_overrides={"analyst": tools or {}}
        ),
    )
    return result, lead, expert


async def test_valid_research_result_returns_without_terminal_call(tmp_path):
    analyst = ScriptModel(output("ResearchResult", job_id="a", outcome="partial", summary="done"))
    result, _, _ = await run_models(tmp_path, analyst)
    assert saved(result)[0]["summary"] == "done"
    assert len(analyst.calls) == 1
    assert transitions(result) == []


async def test_planned_generation_boundary_preserves_history_and_later_capacity(tmp_path):
    analyst = ScriptModel(
        output("knowledge_read", module_id="evidence"),
        output("ResearchResult", job_id="a", outcome="partial", summary="Final only"),
    )
    cfg = terminal_config(
        tmp_path,
        max_generations=6,
        finalization=dict(
            analyst_terminal_seconds=150, analyst_reserve_seconds=180, analyst_reserve_generations=3
        ),
    )
    result, lead, expert = await run_models(tmp_path, analyst, cfg=cfg)
    assert saved(result)[0]["summary"] == "Final only"
    assert len(analyst.calls) == 2 and len(lead.calls) == 3 and len(expert.calls) == 1
    assert {x["name"] for x in analyst.calls[1]["tools"]} == {"ResearchResult"}
    assert analyst.calls[1]["tool_choice"] == {"tool": {"name": "ResearchResult"}}
    assert "knowledge_read" in json.dumps(analyst.calls[1]["messages"])
    (row,) = transitions(result)
    assert row["boundary"] == "generation_reserve"
    assert row["generation_ceiling"] == 3
    assert row["history"] == "actual_agent"
    assert json.loads(Path(result.artifacts["manifest"]).read_text())["generations"] == 6


@pytest.mark.parametrize(
    "step", [output("ResearchResult"), output("knowledge_read", module_id="evidence")]
)
async def test_terminal_accepts_only_one_generation_and_stale_tools_cannot_execute(tmp_path, step):
    executed = []

    async def knowledge_read(module_id: str) -> dict:
        """Count actual execution."""
        executed.append(module_id)
        return {"status": "success"}

    analyst = ScriptModel(
        output("knowledge_read", module_id="evidence"),
        step,
        output("ResearchResult", job_id="a", outcome="success"),
    )
    cfg = terminal_config(
        tmp_path,
        max_generations=6,
        finalization=dict(analyst_terminal_seconds=150, analyst_reserve_generations=3),
    )
    result, _, _ = await run_models(
        tmp_path, analyst, cfg=cfg, tools={"knowledge_read": knowledge_read}
    )
    assert len(analyst.calls) == 2
    assert executed == ["evidence"]
    assert saved(result)[0]["outcome"] == "budget_exhausted"


@pytest.mark.parametrize("repairs,expected_calls,terminal", [(0, 1, False), (1, 2, True)])
async def test_failed_output_does_not_get_fresh_repair_allowance(
    tmp_path, repairs, expected_calls, terminal
):
    analyst = ScriptModel(
        output("ResearchResult"),
        output("ResearchResult"),
        output("ResearchResult", job_id="a", outcome="success"),
    )
    cfg = terminal_config(
        tmp_path,
        max_generations=6,
        finalization=dict(
            analyst_terminal_seconds=150, analyst_reserve_generations=3, max_output_repairs=repairs
        ),
    )
    result, _, _ = await run_models(tmp_path, analyst, cfg=cfg)
    assert len(analyst.calls) == expected_calls
    assert bool(transitions(result)) is terminal
    assert saved(result)[0]["outcome"] == "budget_exhausted"


@pytest.mark.parametrize("error", [TimeoutError("provider transport"), RuntimeError("provider")])
async def test_provider_failure_never_triggers_terminal(tmp_path, error):
    analyst = ScriptModel(error)
    result, _, _ = await run_models(tmp_path, analyst)
    assert len(analyst.calls) == 1 and transitions(result) == []
    assert saved(result)[0]["outcome"] == "failed"


async def test_planned_time_boundary_awaits_cleanup_and_retains_original_deadline(tmp_path):
    stopped = asyncio.Event()

    class WaitingModel(ScriptModel):
        async def stream(self, *args, **kwargs):
            if not self.calls:
                try:
                    async for event in super().stream(*args, **kwargs):
                        yield event
                finally:
                    await asyncio.sleep(0)
                    stopped.set()
            else:
                assert stopped.is_set()
                async for event in super().stream(*args, **kwargs):
                    yield event

    analyst = WaitingModel(asyncio.Event(), output("ResearchResult", job_id="a", outcome="partial"))
    cfg = terminal_config(
        tmp_path,
        deadline_seconds=2,
        finalization=dict(analyst_reserve_seconds=0.5, analyst_terminal_seconds=1.4),
    )
    result, _, _ = await run_models(tmp_path, analyst, cfg=cfg)
    assert len(analyst.calls) == 2 and stopped.is_set()
    (row,) = transitions(result)
    assert row["boundary"] == "time_reserve"
    assert row["analyst_cutoff"] - row["research_cutoff"] == pytest.approx(1.4)
    assert saved(result)[0]["outcome"] == "partial"


async def test_external_cancel_at_research_boundary_awaits_cleanup_and_skips_terminal(tmp_path):
    started, stopped = asyncio.Event(), asyncio.Event()
    task = None

    class CancellingModel(ScriptModel):
        async def stream(self, *args, **kwargs):
            started.set()
            try:
                async for event in super().stream(*args, **kwargs):
                    yield event
            finally:
                task.cancel()  # External cancel racing the local timeout's cleanup.
                await asyncio.sleep(0)
                stopped.set()

    analyst = CancellingModel(asyncio.Event())
    cfg = terminal_config(
        tmp_path,
        deadline_seconds=2,
        finalization=dict(analyst_reserve_seconds=0.5, analyst_terminal_seconds=1.4),
    )
    task = asyncio.create_task(run_models(tmp_path, analyst, cfg=cfg))
    await asyncio.wait_for(started.wait(), 1)
    result, _, _ = await asyncio.wait_for(task, 2)
    assert result.completion_reason == "cancelled"
    assert stopped.is_set() and len(analyst.calls) == 1
    assert transitions(result) == []


async def test_queued_and_late_jobs_cannot_acquire_new_analyst_capacity(tmp_path):
    analyst = ScriptModel(
        output("knowledge_read", module_id="evidence"),
        output("ResearchResult", job_id="a", outcome="partial"),
    )
    cfg = terminal_config(
        tmp_path,
        max_generations=6,
        finalization=dict(analyst_terminal_seconds=150, analyst_reserve_generations=3),
    )
    result, _, _ = await run_models(
        tmp_path,
        analyst,
        cfg=cfg,
        jobs=[{"job_id": name, "question": "Inspect"} for name in ("a", "queued")],
    )
    assert [r["outcome"] for r in saved(result)] == ["partial", "budget_exhausted"]
    assert len(transitions(result)) == 1


async def test_selected_real_records_survive_terminal_history_with_unchanged_prefix(tmp_path):
    """Mechanical SDK transport only; saved observations imply no model/source acceptance."""
    selected = os.environ.get("WHEEL_TEST_EVIDENCE_RUN")
    if not selected:
        pytest.skip("Set WHEEL_TEST_EVIDENCE_RUN to private attempt-4 evidence")
    paths = sorted((Path(selected) / "records").glob("*.json"))
    assert len(paths) == 18
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    envelopes = [json.loads(p.read_text()) for p in paths]
    chosen = next(
        e
        for e in envelopes
        if e["record"].get("content", {}).get("capability") == "underlying_history"
    )
    assert chosen["record"]["content"]["instrument_id"] == "PEP"
    from wheel_team.agents import build_analyst, build_team_lead

    class ObservedPrefixModel(ScriptModel):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            assert system_prompt.startswith(approved_bytes.decode() + "\n\nRun context (data):\n")
            async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
                yield event

    models = deque(
        [
            ScriptModel(
                output("research_batch", jobs=[{"job_id": "a", "question": "Read PEP"}]),
                output("TeamResult", summary="Mechanical archived record check"),
            ),
            ObservedPrefixModel(
                output("evidence_read", record_ref=chosen["ref"]),
                output(
                    "ResearchResult",
                    job_id="a",
                    outcome="partial",
                    evidence_refs=[chosen["ref"]],
                    summary="Mechanical saved reference",
                ),
            ),
        ]
    )
    agents = []
    prefixes = []
    from wheel_team.config import PromptSet

    prompt_manifest = Path("artifacts/thin/owner-ready-20261031/prompts/manifest.json").resolve()
    prompt_metadata = json.loads(prompt_manifest.read_text())
    approved_bytes = (
        prompt_manifest.parent / prompt_metadata["roles"]["analyst"]["path"]
    ).read_bytes()
    assert (
        hashlib.sha256(approved_bytes).hexdigest()
        == prompt_metadata["roles"]["analyst"]["content_hash"]
    )

    def factory(role, ctx):
        if role == "analyst":
            for envelope in envelopes:
                assert ctx.store.put("evidence", envelope["record"]) == envelope["ref"]
        agent = (build_analyst if role == "analyst" else build_team_lead)(ctx)
        if role == "analyst":
            agents.append(agent)
            prefixes.append(ctx.prompt.content)
        return agent

    analyst = models[1]
    cfg = terminal_config(
        tmp_path,
        prompts=PromptSet(manifest_path=prompt_manifest),
        max_generations=5,
        finalization=dict(analyst_terminal_seconds=150, analyst_reserve_generations=2),
    )
    result = await run_team(
        TeamRequest(question="Offline saved PEP", mode="education"),
        config=cfg,
        deps=TeamDependencies(model_factory=lambda *_: models.popleft(), role_factory=factory),
    )
    assert len(agents) == 1 and len(analyst.calls) == 2
    assert saved(result)[0]["evidence_refs"] == [chosen["ref"]]
    messages = analyst.calls[1]["messages"]
    tool_result = next(
        block["toolResult"] for msg in messages for block in msg["content"] if "toolResult" in block
    )
    observed = json.loads(tool_result["content"][0]["text"])
    assert observed["data"]["record"] == chosen["record"]
    assert observed["result_refs"] == [chosen["ref"]]
    assert prefixes[0].encode() == approved_bytes
    assert agents[0].system_prompt.startswith(prefixes[0] + "\n\nRun context (data):\n")
    assert {s["name"] for s in analyst.calls[1]["tools"]} == {"ResearchResult"}
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths} == before


async def test_time_boundary_after_accepted_repair_does_not_start_terminal_phase(tmp_path):
    analyst = ScriptModel(output("ResearchResult"), asyncio.Event())
    cfg = terminal_config(
        tmp_path,
        deadline_seconds=2,
        finalization=dict(
            analyst_reserve_seconds=0.5, analyst_terminal_seconds=1.4, max_output_repairs=1
        ),
    )
    result, _, _ = await run_models(tmp_path, analyst, cfg=cfg)
    assert len(analyst.calls) == 2
    assert transitions(result) == []
    assert "output_repairs" in saved(result)[0]["error"]


async def test_original_absolute_phase_and_global_capacity_remain_enforced(tmp_path):
    contexts = []
    from wheel_team.agents import build_analyst, build_team_lead

    class ExhaustingModel(ScriptModel):
        async def stream(self, *args, **kwargs):
            try:
                async for event in super().stream(*args, **kwargs):
                    yield event
            finally:
                contexts[0].budget.generations = 24

    analyst = ExhaustingModel(asyncio.Event())
    models = deque(
        [
            ScriptModel(
                output("research_batch", jobs=[{"job_id": "a", "question": "Wait"}]),
                output("TeamResult", summary="must not start"),
            ),
            analyst,
        ]
    )

    def factory(role, ctx):
        if role == "analyst":
            contexts.append(ctx)
            assert ctx.generation_ceiling == 18
            assert ctx.monotonic_deadline - ctx.invocation_deadline == pytest.approx(0.5)
        return (build_analyst if role == "analyst" else build_team_lead)(ctx)

    result = await run_team(
        TeamRequest(question="Exhaust during cleanup", mode="education"),
        config=terminal_config(
            tmp_path,
            deadline_seconds=2,
            finalization=dict(
                analyst_reserve_seconds=0.5,
                analyst_reserve_generations=6,
                analyst_terminal_seconds=1.4,
            ),
        ),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft(), role_factory=factory),
    )
    assert len(analyst.calls) == 1 and transitions(result) == []
    assert saved(result)[0]["outcome"] == "budget_exhausted"


async def test_schema_valid_output_wins_sdk_turn_limit_stop_label(monkeypatch):
    from types import SimpleNamespace

    from strands import Agent

    from wheel_team.team import _invoke
    from wheel_team.types import ResearchResult

    async def invoke(self, *args, **kwargs):
        return SimpleNamespace(
            stop_reason="limit_turns",
            structured_output=ResearchResult(
                job_id="a", outcome="partial", summary="SDK completed output"
            ),
        )

    monkeypatch.setattr(Agent, "invoke_async", invoke)
    agent = Agent(model=ScriptModel(), callback_handler=None)
    assert (await _invoke(agent, "Final phase", ResearchResult, terminal=True)).summary == (
        "SDK completed output"
    )


@pytest.mark.parametrize(
    "error", [TimeoutError("terminal provider"), RuntimeError("terminal provider")]
)
async def test_terminal_provider_failure_retains_failure_category(tmp_path, error):
    analyst = ScriptModel(output("knowledge_read", module_id="evidence"), error)
    cfg = terminal_config(
        tmp_path,
        max_generations=6,
        finalization=dict(analyst_terminal_seconds=150, analyst_reserve_generations=3),
    )
    result, _, _ = await run_models(tmp_path, analyst, cfg=cfg)
    assert len(analyst.calls) == 2 and len(transitions(result)) == 1
    assert saved(result)[0]["outcome"] == "failed"
    assert saved(result)[0]["error"] == "Analyst failed (" + type(error).__name__ + ")"


async def test_trial_split_is_210_360_seconds_and_shared_generations_17_18(tmp_path):
    class BudgetModel(ScriptModel):
        def __init__(self, *steps):
            super().__init__(*steps)
            self.runtime = []

        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            self.runtime.append(
                json.loads(system_prompt.split("Run context (data):\n")[1])["runtime_budget"]
            )
            async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
                yield event

    analyst = BudgetModel(
        *[output("knowledge_read", module_id="evidence") for _ in range(16)],
        output("ResearchResult", job_id="a", outcome="partial"),
    )
    result, _, expert = await run_models(tmp_path, analyst)
    assert len(analyst.calls) == 17 and len(expert.calls) == 1
    (row,) = transitions(result)
    assert row["generation_ceiling"] == 18
    assert row["analyst_cutoff"] - row["research_cutoff"] == 150
    assert 209 < analyst.runtime[0]["invocation_seconds_remaining"] <= 210
    assert 359 < analyst.runtime[-1]["invocation_seconds_remaining"] <= 360
    assert analyst.runtime[-2]["shared_generations_remaining"] == 7
    assert analyst.runtime[-1]["shared_generations_remaining"] == 6
    assert analyst.runtime[-2]["invocation_generations_remaining"] == 0
    assert analyst.runtime[-1]["invocation_generations_remaining"] == 0
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert manifest["limits"]["deadline_seconds"] == 540
    assert manifest["limits"]["max_generations"] == 24
    assert manifest["generations"] == 21


async def test_cleanup_crossing_original_analyst_cutoff_cannot_start_terminal(tmp_path):
    cleaned = asyncio.Event()
    release_cleanup = asyncio.Event()

    class SlowCleanupModel(ScriptModel):
        async def stream(self, *args, **kwargs):
            try:
                async for event in super().stream(*args, **kwargs):
                    yield event
            finally:
                asyncio.get_running_loop().call_later(0.2, release_cleanup.set)
                await release_cleanup.wait()
                cleaned.set()

    analyst = SlowCleanupModel(asyncio.Event())
    cfg = terminal_config(
        tmp_path,
        deadline_seconds=1,
        finalization=dict(analyst_reserve_seconds=0.75, analyst_terminal_seconds=0.15),
    )
    result, _, _ = await run_models(tmp_path, analyst, cfg=cfg)
    assert cleaned.is_set() and len(analyst.calls) == 1
    assert transitions(result) == []
    assert "time_reserve" in saved(result)[0]["error"]


async def test_terminal_before_model_gate_rejects_elapsed_original_monotonic_cutoff(tmp_path):
    from tests.thin.test_agents import context
    from wheel_team.agents import build_analyst
    from wheel_team.models import invocation_failure
    from wheel_team.types import ResearchResult

    model = ScriptModel(output("ResearchResult", job_id="a", outcome="partial"))
    ctx = context(tmp_path, "analyst", model)
    ctx.terminal_only = True
    ctx.invocation_deadline = asyncio.get_running_loop().time() - 1
    with pytest.raises(Exception) as caught:
        await build_analyst(ctx).invoke_async(
            "Terminal after original cutoff",
            structured_output_model=ResearchResult,
            limits={"turns": 1},
        )
    assert invocation_failure(caught.value) == "time_reserve"
    assert model.calls == [] and ctx.budget.generations == 0


@pytest.mark.parametrize("external_cancel", [False, True])
async def test_research_cleanup_failure_prevents_terminal_with_cancel_precedence(
    tmp_path, external_cancel
):
    started, cleaned = asyncio.Event(), asyncio.Event()

    class FailingCleanupModel(ScriptModel):
        async def stream(self, *args, **kwargs):
            if not self.calls:
                started.set()
                try:
                    async for event in super().stream(*args, **kwargs):
                        yield event
                finally:
                    cleaned.set()
                    raise RuntimeError("offline cleanup failed")
            else:
                async for event in super().stream(*args, **kwargs):
                    yield event

    analyst = FailingCleanupModel(
        asyncio.Event(), output("ResearchResult", job_id="a", outcome="partial")
    )
    cfg = terminal_config(
        tmp_path,
        deadline_seconds=2,
        finalization=dict(analyst_reserve_seconds=0.5, analyst_terminal_seconds=1.4),
    )
    task = asyncio.create_task(run_models(tmp_path, analyst, cfg=cfg))
    await asyncio.wait_for(started.wait(), 1)
    if external_cancel:
        task.cancel()
    result, _, _ = await asyncio.wait_for(task, 2)
    assert cleaned.is_set() and len(analyst.calls) == 1
    assert transitions(result) == []
    if external_cancel:
        assert result.completion_reason == "cancelled"
        assert saved(result)[0]["outcome"] == "cancelled"
    else:
        assert saved(result)[0]["outcome"] == "failed"
        assert saved(result)[0]["error"] == "Analyst failed (RuntimeError)"


@pytest.mark.parametrize("extra_cancels", [False, True])
async def test_owned_research_cleanup_is_awaited_through_original_and_global_deadlines(
    tmp_path, extra_cancels
):
    cleaning, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class HeldCleanupModel(ScriptModel):
        async def stream(self, *args, **kwargs):
            try:
                async for event in super().stream(*args, **kwargs):
                    yield event
            finally:
                cleaning.set()
                await release.wait()
                cleaned.set()

    analyst = HeldCleanupModel(asyncio.Event())
    cfg = terminal_config(
        tmp_path,
        deadline_seconds=0.4,
        finalization=dict(analyst_reserve_seconds=0.2, analyst_terminal_seconds=0.1),
    )
    before = asyncio.all_tasks()
    task = asyncio.create_task(run_models(tmp_path, analyst, cfg=cfg))
    await asyncio.wait_for(cleaning.wait(), 1)
    if extra_cancels:
        for delay in (0.13, 0.22, 0.32):
            asyncio.get_running_loop().call_later(delay, task.cancel)
    # Cross both absolute cutoffs while the real SDK transport's cleanup stays held.
    crossed_deadlines = asyncio.Event()
    asyncio.get_running_loop().call_later(0.4, crossed_deadlines.set)
    await crossed_deadlines.wait()
    returned_before_release = task.done()
    release.set()
    result, _, _ = await asyncio.wait_for(asyncio.shield(task), 1)
    # Allow an already detached SDK task to settle as well, so RED leaves no task behind.
    await asyncio.sleep(0)
    assert not returned_before_release
    assert cleaned.is_set() and len(analyst.calls) == 1
    assert result.completion_reason == ("cancelled" if extra_cancels else "deadline")
    assert transitions(result) == []
    assert not (asyncio.all_tasks() - before - {task})
