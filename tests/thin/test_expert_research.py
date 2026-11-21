"""Expert waits for host-scheduled public research inside one assessment."""

import asyncio
import json
from collections import deque
from pathlib import Path

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_analyst_report_access import expert_data, tool_data
from tests.thin.test_direct_text_handoff import direct_config
from tests.thin.test_explorer_flow import explorer_config
from wheel_team.team import run_team
from wheel_team.types import TeamDependencies, TeamRequest


async def test_same_expert_gets_original_followup_and_reuses_initial_job_without_deadlock(tmp_path):
    lead = ScriptModel(
        output("research_batch", jobs=[{"job_id": "initial", "question": "Inspect public A"}]),
        output("consult_expert"),
        {"text": "Delivered"},
    )
    first = ScriptModel({"text": "  Exact initial α\r\nOriginal caveat.  "})
    expert = ScriptModel(
        output("request_research", jobs=[{"job_id": "extra", "question": "Inspect public B"}]),
        output("request_research", jobs=[{"job_id": "same-again", "question": "Inspect public A"}]),
        {"text": "One assessment after extra evidence"},
    )
    extra = ScriptModel({"text": "  Exact additional β\nDated evidence.  "})
    models = deque([lead, first, expert, extra])
    result = await asyncio.wait_for(
        run_team(
            TeamRequest(question="Private task", mode="combined"),
            config=direct_config(tmp_path).model_copy(update={"analyst_concurrency": 1}),
            deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
        ),
        timeout=5,
    )
    assert result.completion_reason == "complete" and not models
    initial = expert_data(expert)["analyst_reports"][0]
    added = tool_data(expert.calls[1])
    reused = tool_data(expert.calls[2])
    assert added["reports"][0]["summary"] == "  Exact additional β\nDated evidence.  "
    assert reused["reports"] == [initial]
    assert reused["request_mapping"] == [
        {"requested_job_id": "same-again", "original_job_id": "initial", "reused": True}
    ]
    saved = json.loads(Path(result.artifacts["research"]).read_text())
    assert saved == [initial, added["reports"][0]]
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert manifest["expert_consultations"] == 1


async def test_expert_round_and_new_job_limits_do_not_launch_extra_models(tmp_path):
    lead = ScriptModel(output("consult_expert"), {"text": "Delivered"})
    expert = ScriptModel(
        output(
            "request_research",
            jobs=[{"job_id": "a", "question": "Public A"}, {"job_id": "b", "question": "Public B"}],
        ),
        output("request_research", jobs=[{"job_id": "a", "question": "Public A"}]),
        {"text": "Limits explained"},
    )
    models = deque([lead, expert])
    result = await run_team(
        TeamRequest(question="Task", mode="education"),
        config=direct_config(tmp_path).model_copy(
            update={"max_expert_research_rounds": 1, "max_expert_research_jobs": 1}
        ),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.completion_reason == "complete" and not models
    assert tool_data(expert.calls[1])["status"] == "limit_reached"
    assert tool_data(expert.calls[2])["status"] == "limit_reached"
    assert json.loads(Path(result.artifacts["research"]).read_text()) == []


async def test_full_pm_review_obtains_fresh_discovery_before_expert_when_lead_omits_it(tmp_path):
    lead = ScriptModel(output("consult_expert"), {"text": "Delivered"})
    explorer = ScriptModel({"text": "Fresh public opportunities; none require a trade"})
    expert = ScriptModel({"text": "Assessment"})
    models = deque([lead, explorer, expert])
    result = await run_team(
        TeamRequest(question="Review", mode="combined"),
        config=explorer_config(tmp_path).model_copy(update={"require_fresh_opportunities": True}),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.completion_reason == "complete" and not models
    assert (
        expert_data(expert)["explorer_reports"][0]["summary"]
        == "Fresh public opportunities; none require a trade"
    )


async def test_pm_full_report_without_discovery_cannot_claim_complete(tmp_path):
    result = await run_team(
        TeamRequest(question="Review", mode="combined"),
        config=explorer_config(tmp_path).model_copy(update={"require_fresh_opportunities": True}),
        deps=TeamDependencies(model_factory=lambda *_: ScriptModel({"text": "Skipped discovery"})),
    )
    assert result.completion_reason == "partial"
    assert any("opportunit" in warning.lower() for warning in result.warnings)


async def test_concurrent_duplicate_requests_share_pending_original(tmp_path):
    from wheel_team.types import ExpertResult, ResearchResult, TeamResult

    invocations = []
    observed = []

    class Role:
        def __init__(self, role, ctx):
            self.role, self.ctx = role, ctx
            invocations.append(role)

        async def invoke_async(self, question):
            if self.role == "team_lead":
                response = await self.ctx.tools["consult_expert"]()
                assert response["error"] is None
                return TeamResult(summary="Delivered")
            if self.role == "wheel_expert":
                tool = self.ctx.tools["request_research"]
                observed.extend(
                    await asyncio.gather(
                        tool([{"job_id": "first", "question": "Same public request"}]),
                        tool([{"job_id": "second", "question": "Same public request"}]),
                    )
                )
                return ExpertResult(assessment="Integrated original evidence")
            await asyncio.sleep(0.01)
            return ResearchResult(
                job_id="first",
                outcome="partial",
                summary="Original limited evidence",
                error="Quote unavailable",
            )

    from tests.thin.test_entrypoint import config

    result = await run_team(
        TeamRequest(question="Review", mode="combined"),
        config=config(tmp_path).model_copy(update={"analyst_concurrency": 1}),
        deps=TeamDependencies(role_factory=lambda role, ctx: Role(role, ctx)),
    )
    assert result.completion_reason == "complete"
    assert invocations == ["team_lead", "wheel_expert", "analyst"]
    assert observed[0]["reports"] == observed[1]["reports"]
    assert observed[1]["request_mapping"][0]["reused"] is True
    assert observed[0]["status"] == "partial"


async def test_additional_research_cannot_reset_shared_generation_budget(tmp_path):
    lead = ScriptModel(output("consult_expert"), {"text": "Unreachable"})
    expert = ScriptModel(
        output("request_research", jobs=[{"job_id": "extra", "question": "Public facts"}]),
        {"text": "Unreachable"},
    )
    models = deque([lead, expert])
    result = await run_team(
        TeamRequest(question="Review", mode="combined"),
        config=direct_config(tmp_path).model_copy(update={"max_generations": 2}),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert not models and result.completion_reason == "partial"
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert manifest["generations"] == 2
    assert json.loads(Path(result.artifacts["research"]).read_text()) == []


async def test_deadline_cancels_extra_analyst_and_preserves_original_initial_report(tmp_path):
    started = asyncio.Event()
    stopped = asyncio.Event()

    class Slow(ScriptModel):
        async def stream(self, *args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
            if False:
                yield {}

    models = deque(
        [
            ScriptModel(
                output("research_batch", jobs=[{"job_id": "initial", "question": "Initial facts"}]),
                output("consult_expert"),
            ),
            ScriptModel({"text": "Original dated initial evidence"}),
            ScriptModel(
                output(
                    "request_research", jobs=[{"job_id": "extra", "question": "Additional facts"}]
                )
            ),
            Slow(),
        ]
    )
    result = await asyncio.wait_for(
        run_team(
            TeamRequest(question="Review", mode="combined"),
            config=direct_config(tmp_path).model_copy(
                update={"deadline_seconds": 0.3, "analyst_concurrency": 1}
            ),
            deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
        ),
        timeout=5,
    )
    assert result.completion_reason == "deadline" and started.is_set() and stopped.is_set()
    reports = json.loads(Path(result.artifacts["research"]).read_text())
    assert reports[0]["summary"] == "Original dated initial evidence"
    assert reports[1]["job_id"] == "extra" and reports[1]["outcome"] == "cancelled"
