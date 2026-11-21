"""Child limits preserve finite capacity for an actual expert and lead continuation."""

import asyncio
import json
from collections import deque
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_entrypoint import config
from wheel_team.config import TeamConfig
from wheel_team.team import run_team
from wheel_team.types import TeamDependencies, TeamRequest


def bounded_config(tmp_path, **values):
    data = config(tmp_path).model_dump(mode="json")
    data.update(values)
    return TeamConfig.model_validate(data)


@pytest.mark.parametrize(
    "policy",
    [
        {"analyst_reserve_seconds": -1},
        {"analyst_reserve_seconds": float("inf")},
        {"analyst_reserve_seconds": True},
        {"analyst_reserve_seconds": "1"},
        {"analyst_reserve_generations": True},
        {"max_output_repairs": -1},
        {"analyst_reserve_seconds": 10},
        {"analyst_reserve_generations": 8},
        {"analyst_reserve_seconds": 2, "expert_reserve_seconds": 3},
        {"analyst_reserve_generations": 1, "expert_reserve_generations": 2},
    ],
)
def test_reservations_must_fit_run_and_preserve_role_order(tmp_path, policy):
    with pytest.raises(ValidationError):
        bounded_config(tmp_path, deadline_seconds=10, max_generations=8, finalization=policy)


async def test_analyst_generation_reserve_allows_expert_and_final_lead(tmp_path):
    lead = ScriptModel(
        output("research_batch", jobs=[{"job_id": "a", "question": "Read available data"}]),
        output("consult_expert", question="Review saved evidence and limitations", idea_refs=[]),
        output("TeamResult", summary="Expert reviewed the available evidence; gaps remain."),
    )
    analyst = ScriptModel(
        output("evidence_find", kind="evidence"),
        output("evidence_find", kind="evidence"),
        output("ResearchResult", job_id="a", outcome="success", summary="must not start"),
    )
    expert = ScriptModel(
        output("ExpertResult", assessment="No actionable contract is established.")
    )
    models = deque([lead, analyst, expert])
    result = await run_team(
        TeamRequest(question="Research then review", mode="focused"),
        config=bounded_config(
            tmp_path,
            max_generations=6,
            finalization={"analyst_reserve_generations": 3, "expert_reserve_generations": 1},
        ),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.completion_reason == "complete"
    assert len(analyst.calls) == 2
    assert len(expert.calls) == 1
    children = json.loads(Path(result.artifacts["research"]).read_text())
    assert children[0]["outcome"] == "budget_exhausted"
    assert "reserved" in children[0]["error"].lower()
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert manifest["generations"] == 6
    assert manifest["limits"]["finalization"]["analyst_reserve_generations"] == 3
    assert "No actionable contract" in Path(result.artifacts["expert"]).read_text()


async def test_expert_generation_reserve_preserves_final_lead(tmp_path):
    lead = ScriptModel(
        output("consult_expert", question="Inspect", idea_refs=[]),
        output("TeamResult", summary="Expert could not complete; no investment judgment."),
    )
    expert = ScriptModel(
        output("evidence_find", kind="evidence"),
        output("ExpertResult", assessment="must not start"),
    )
    models = deque([lead, expert])
    result = await run_team(
        TeamRequest(question="Inspect", mode="education"),
        config=bounded_config(
            tmp_path,
            max_generations=3,
            finalization={"analyst_reserve_generations": 1, "expert_reserve_generations": 1},
        ),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.completion_reason == "complete"
    assert len(expert.calls) == 1
    saved = json.loads(Path(result.artifacts["expert"]).read_text())
    assert "reserved" in saved[0]["error"].lower()


async def test_active_and_queued_analysts_share_cutoff_and_lead_continues(tmp_path):
    started, stopped = asyncio.Event(), asyncio.Event()

    class WaitingModel(ScriptModel):
        async def stream(self, *args, **kwargs):
            started.set()
            try:
                async for event in super().stream(*args, **kwargs):
                    yield event
            finally:
                stopped.set()

    lead = ScriptModel(
        output(
            "research_batch",
            jobs=[{"job_id": name, "question": "Wait"} for name in ("active", "queued")],
        ),
        output("consult_expert", question="Review source gaps", idea_refs=[]),
        output("TeamResult", summary="Research cutoff reached; expert reviewed the gaps."),
    )
    analyst = WaitingModel(asyncio.Event())
    expert = ScriptModel(output("ExpertResult", assessment="Evidence remains insufficient."))
    models = deque([lead, analyst, expert])
    task = asyncio.create_task(
        run_team(
            TeamRequest(question="Research", mode="focused"),
            config=bounded_config(
                tmp_path,
                analyst_concurrency=1,
                deadline_seconds=4,
                finalization={"analyst_reserve_seconds": 3, "expert_reserve_seconds": 1},
            ),
            deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    result = await asyncio.wait_for(task, 3)
    assert stopped.is_set()
    assert result.completion_reason == "complete"
    assert not models  # The queued analyst must never create its own model.
    children = json.loads(Path(result.artifacts["research"]).read_text())
    assert {child["job_id"] for child in children} == {"active", "queued"}
    assert all(child["outcome"] == "budget_exhausted" for child in children)
    assert all("reserved" in child["error"].lower() for child in children)
    assert len(expert.calls) == 1


async def test_dispatch_after_analyst_reservation_creates_no_child(tmp_path):
    created = []

    class Role:
        def __init__(self, role, context):
            created.append(role)
            self.role, self.context = role, context

        async def invoke_async(self, question):
            assert self.role == "team_lead"
            self.context.budget.generations = 2
            children = await self.context.tools["research_batch"](
                [{"job_id": "late", "question": "Too late"}]
            )
            assert children[0]["outcome"] == "budget_exhausted"
            return {"summary": "Bounded before child creation."}

    result = await run_team(
        TeamRequest(question="Inspect", mode="education"),
        config=bounded_config(
            tmp_path, max_generations=3, finalization={"analyst_reserve_generations": 1}
        ),
        deps=TeamDependencies(role_factory=Role),
    )
    assert result.completion_reason == "complete"
    assert created == ["team_lead"]


async def test_provider_timeout_is_not_mislabeled_as_reserved_capacity(tmp_path):
    class Role:
        def __init__(self, role, context):
            self.role, self.context = role, context

        async def invoke_async(self, question):
            if self.role == "analyst":
                raise TimeoutError("Provider timed out before the local cutoff")
            children = await self.context.tools["research_batch"](
                [{"job_id": "timeout", "question": "Read source"}]
            )
            assert children[0]["outcome"] == "failed"
            assert children[0]["error"] == "Analyst failed (TimeoutError)"
            return {"summary": "Source transport did not complete."}

    result = await run_team(
        TeamRequest(question="Inspect", mode="education"),
        config=bounded_config(
            tmp_path, deadline_seconds=10, finalization={"analyst_reserve_seconds": 2}
        ),
        deps=TeamDependencies(role_factory=Role),
    )
    assert result.completion_reason == "complete"


async def test_expert_cutoff_awaits_stream_cleanup_and_lead_finishes(tmp_path):
    started, stopped = asyncio.Event(), asyncio.Event()

    class WaitingExpert(ScriptModel):
        async def stream(self, *args, **kwargs):
            started.set()
            try:
                async for event in super().stream(*args, **kwargs):
                    yield event
            finally:
                stopped.set()

    lead = ScriptModel(
        output("consult_expert", question="Review limitations", idea_refs=[]),
        output("TeamResult", summary="Expert reached its cutoff; no final investment judgment."),
    )
    expert = WaitingExpert(asyncio.Event())
    models = deque([lead, expert])
    task = asyncio.create_task(
        run_team(
            TeamRequest(question="Inspect", mode="education"),
            config=bounded_config(
                tmp_path,
                deadline_seconds=3,
                finalization={"analyst_reserve_seconds": 2, "expert_reserve_seconds": 2},
            ),
            deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    result = await asyncio.wait_for(task, 3)
    assert stopped.is_set()
    assert result.completion_reason == "complete"
    saved = json.loads(Path(result.artifacts["expert"]).read_text())
    assert "time_reserve" in saved[0]["error"]


async def test_external_cancel_with_reserves_retains_global_cancellation(tmp_path):
    started, stopped = asyncio.Event(), asyncio.Event()
    created = []

    class Role:
        def __init__(self, role, context):
            created.append(role)
            self.role, self.context = role, context

        async def invoke_async(self, question):
            if self.role == "team_lead":
                return await self.context.tools["research_batch"](
                    [{"job_id": name, "question": "Wait"} for name in ("active", "queued")]
                )
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                stopped.set()

    task = asyncio.create_task(
        run_team(
            TeamRequest(question="Research", mode="focused"),
            config=bounded_config(
                tmp_path,
                analyst_concurrency=1,
                deadline_seconds=10,
                finalization={"analyst_reserve_seconds": 5},
            ),
            deps=TeamDependencies(role_factory=Role),
        )
    )
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    result = await asyncio.wait_for(task, 2)
    assert result.completion_reason == "cancelled"
    assert stopped.is_set()
    assert created == ["team_lead", "analyst"]
    children = json.loads(Path(result.artifacts["research"]).read_text())
    assert len(children) == 2
    assert all(child["outcome"] == "cancelled" for child in children)
