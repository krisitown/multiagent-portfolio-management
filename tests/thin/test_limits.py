"""All model attempts, including SDK repairs, consume one shared finite budget."""

import asyncio
import json
from collections import deque
from pathlib import Path

import pytest
from strands.types.exceptions import ModelThrottledException

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_entrypoint import config
from portfolio_management.models import GenerationLimitExceeded
from portfolio_management.team import run_team
from portfolio_management.types import TeamDependencies, TeamRequest


async def test_fifth_expert_allowed_sixth_rejected_without_spawning(tmp_path):
    lead = ScriptModel(
        *[output("consult_expert", question=f"Consult {i}", idea_refs=[]) for i in range(6)],
        output("TeamResult", summary="bounded"),
    )
    models = deque(
        [lead] + [ScriptModel(output("ExpertResult", assessment=f"review {i}")) for i in range(5)]
    )
    result = await run_team(
        TeamRequest(question="Review", mode="education"),
        config=config(tmp_path).model_copy(update={"max_expert_consultations": 5}),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.summary == "bounded"
    assert not models
    messages = json.dumps(lead.calls[-1]["messages"])
    assert "Expert consultation limit" in messages
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert manifest["expert_consultations"] == 5


@pytest.mark.parametrize(
    "steps,limit,count,reason",
    [
        (
            [output("TeamResult", summary=1), output("TeamResult", summary="repaired")],
            2,
            2,
            "complete",
        ),
        (
            [output("TeamResult", summary=1), output("TeamResult", summary="unreached")],
            1,
            1,
            "partial",
        ),
        ([{"text": "format this"}, {"text": '{"summary":"forced JSON"}'}], 2, 2, "complete"),
        ([{"text": "format this"}, {"text": '{"summary":"unreached"}'}], 1, 1, "partial"),
    ],
)
async def test_generation_ceiling_includes_repairs_and_forced_formatting(
    tmp_path, steps, limit, count, reason
):
    model = ScriptModel(*steps)
    result = await run_team(
        TeamRequest(question="Explain", mode="education"),
        config=config(tmp_path).model_copy(update={"max_generations": limit}),
        deps=TeamDependencies(model_factory=lambda *_: model),
    )
    assert len(model.calls) == count
    assert result.completion_reason == reason
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert manifest["generations"] == count
    if reason == "partial":
        assert "generation" in " ".join(result.warnings).lower()


async def test_sdk_throttle_is_not_automatically_retried_and_failure_is_saved(tmp_path):
    model = ScriptModel(
        ModelThrottledException("offline throttle"), output("TeamResult", summary="wrong")
    )
    result = await run_team(
        TeamRequest(question="Explain", mode="education"),
        config=config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: model),
    )
    assert len(model.calls) == 1
    assert result.completion_reason == "failed"
    assert Path(result.artifacts["result"]).is_file()


@pytest.mark.parametrize("cancel", [False, True])
async def test_deadline_and_external_cancel_await_children_and_save_partial(tmp_path, cancel):
    started = asyncio.Event()
    finished = asyncio.Event()

    class Role:
        def __init__(self, role, context):
            self.role, self.context = role, context

        async def invoke_async(self, question):
            if self.role == "team_lead":
                return await self.context.tools["research_batch"](
                    [
                        {"job_id": "slow", "question": "Wait"},
                    ]
                )
            (self.context.run_dir / "records" / "completed.json").write_text(
                '{"value":"preserved"}'
            )
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                finished.set()

    cfg = config(tmp_path).model_copy(update={"deadline_seconds": 60 if cancel else 0.03})
    task = asyncio.create_task(
        run_team(
            TeamRequest(question="Research", mode="focused"),
            config=cfg,
            deps=TeamDependencies(role_factory=Role),
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    if cancel:
        task.cancel()
    result = await asyncio.wait_for(task, 2)
    assert finished.is_set()
    assert result.completion_reason == ("cancelled" if cancel else "deadline")
    assert (Path(result.artifacts["result"]).parent / "records" / "completed.json").is_file()
    assert Path(result.artifacts["report"]).is_file()


async def test_generation_budget_is_shared_across_parent_and_children(tmp_path):
    lead = ScriptModel(
        output(
            "research_batch",
            jobs=[
                {"job_id": "a", "question": "A"},
                {"job_id": "b", "question": "B"},
            ],
        ),
        output("TeamResult", summary="must not dispatch"),
    )
    a = ScriptModel(output("ResearchResult", job_id="a", outcome="success", summary="A complete"))
    b = ScriptModel(output("ResearchResult", job_id="b", outcome="success", summary="B complete"))
    models = deque([lead, a, b])
    result = await run_team(
        TeamRequest(question="Research", mode="focused"),
        config=config(tmp_path).model_copy(update={"max_generations": 3}),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert sum(len(model.calls) for model in (lead, a, b)) == 3
    assert result.completion_reason == "partial"
    children = json.loads(Path(result.artifacts["research"]).read_text())
    assert [child["summary"] for child in children] == ["A complete", "B complete"]


async def test_explicit_sdk_retry_consumes_the_same_generation_budget(tmp_path):
    from strands.hooks import HookProvider
    from strands.hooks.events import AfterModelCallEvent

    from tests.thin.test_agents import context
    from portfolio_management.agents.team_lead import build_team_lead

    class RetryOnce(HookProvider):
        def __init__(self):
            self.requested = False

        def register_hooks(self, registry, **kwargs):
            registry.add_callback(AfterModelCallEvent, self.retry)

        def retry(self, event):
            if not self.requested:
                self.requested = True
                event.retry = True

    model = ScriptModel({"text": "discard"}, {"text": "second dispatch forbidden"})
    ctx = context(tmp_path, "team_lead", model, limit=1)
    agent = build_team_lead(ctx)
    agent.hooks.add_hook(RetryOnce())
    with pytest.raises(GenerationLimitExceeded):
        await agent.invoke_async("Retry once")
    assert len(model.calls) == 1
    assert ctx.budget.generations == 1


async def test_deadline_cancels_actual_sdk_model_wait_and_closes_run(tmp_path):
    started, stopped = asyncio.Event(), asyncio.Event()

    class WaitingModel(ScriptModel):
        async def stream(self, *args, **kwargs):
            started.set()
            try:
                async for event in super().stream(*args, **kwargs):
                    yield event
            finally:
                stopped.set()

    model = WaitingModel(asyncio.Event())
    task = asyncio.create_task(
        run_team(
            TeamRequest(question="Wait", mode="education"),
            config=config(tmp_path).model_copy(update={"deadline_seconds": 2}),
            deps=TeamDependencies(model_factory=lambda *_: model),
        )
    )
    # Prove the SDK reached the blocked transport before asserting deadline cleanup.
    # The old 20 ms total deadline could expire during SDK initialization instead.
    await asyncio.wait_for(started.wait(), 1)
    assert not task.done()
    result = await asyncio.wait_for(task, 3)
    assert result.completion_reason == "deadline"
    assert len(model.calls) == 1
    assert stopped.is_set()
    assert Path(result.artifacts["result"]).is_file()


async def test_raw_provider_failure_text_is_absent_from_artifacts(tmp_path):
    secret = "sk-test-should-never-leak"
    model = ScriptModel(RuntimeError("HTTP response Authorization: Bearer " + secret))
    result = await run_team(
        TeamRequest(question="Explain", mode="education"),
        config=config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: model),
    )
    assert result.completion_reason == "failed"
    assert all(secret not in Path(path).read_text() for path in result.artifacts.values())
