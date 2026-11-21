"""Actual callable delegation returns independent results and expert follow-up."""

import asyncio
import json
from collections import deque
from pathlib import Path

import pytest

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_entrypoint import config
from wheel_team.team import run_team
from wheel_team.types import TeamDependencies, TeamRequest


async def test_actual_sdk_batch_and_expert_followup_reach_lead(tmp_path):
    models = deque(
        [
            ScriptModel(
                output(
                    "research_batch",
                    jobs=[
                        {"job_id": "a", "question": "Investigate A"},
                        {"job_id": "b", "question": "Investigate B"},
                    ],
                ),
                output("consult_expert", question="Review findings", idea_refs=[]),
                output("TeamResult", summary="lead synthesis", report_markdown="# Lead synthesis"),
            ),
            ScriptModel(
                output("ResearchResult", job_id="a", outcome="success", summary="A evidence")
            ),
            ScriptModel(RuntimeError("synthetic analyst transport failure")),
            ScriptModel(
                output(
                    "ExpertResult",
                    assessment="needs follow-up",
                    requested_follow_up=[
                        {"job_id": "follow", "question": "Check the missing term"},
                    ],
                )
            ),
        ]
    )
    retained = list(models)
    result = await run_team(
        TeamRequest(question="Investigate independently", mode="focused"),
        config=config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.summary == "lead synthesis"
    lead_messages = json.dumps(retained[0].calls[-1]["messages"])
    assert "A evidence" in lead_messages
    assert "failed" in lead_messages
    assert "Check the missing term" in lead_messages
    rows = [json.loads(line) for line in Path(result.artifacts["trace"]).read_text().splitlines()]
    calls = [r["name"] for r in rows if r["event"] == "tool_call_started"]
    assert "research_batch" in calls and "consult_expert" in calls
    assert json.loads(Path(result.artifacts["manifest"]).read_text())["generations"] == 6


@pytest.mark.parametrize("sequence", [[], ["consult_expert"], ["research_batch", "consult_expert"]])
async def test_lead_model_controls_valid_tool_sequence(tmp_path, sequence):
    steps = [
        output(
            name,
            **(
                {"jobs": []}
                if name == "research_batch"
                else {
                    "question": "Check",
                    "idea_refs": [],
                }
            ),
        )
        for name in sequence
    ]
    lead = ScriptModel(*steps, output("TeamResult", summary="chosen"))
    models = deque([lead, ScriptModel(output("ExpertResult", assessment="fine"))])
    result = await run_team(
        TeamRequest(question="Explain", mode="education"),
        config=config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    rows = [json.loads(s) for s in Path(result.artifacts["trace"]).read_text().splitlines()]
    actual = [
        r["name"]
        for r in rows
        if r["event"] == "tool_call_started" and r["name"] in {"research_batch", "consult_expert"}
    ]
    assert actual == sequence
    assert result.summary == "chosen"


@pytest.mark.parametrize("concurrency", [2, 4])
async def test_child_semaphore_allows_configured_concurrency_and_releases_parent(
    tmp_path, concurrency
):
    active = maximum = 0
    release = asyncio.Event()
    started = asyncio.Event()

    class Role:
        def __init__(self, role, context):
            self.role, self.context = role, context

        async def invoke_async(self, question):
            nonlocal active, maximum
            if self.role == "team_lead":
                task = asyncio.create_task(
                    self.context.tools["research_batch"](
                        [{"job_id": str(i), "question": f"Question {i}"} for i in range(6)]
                    )
                )
                await asyncio.wait_for(started.wait(), 1)
                release.set()
                results = await task
                return {"summary": str(len(results))}
            active += 1
            maximum = max(maximum, active)
            if active == concurrency:
                started.set()
            await release.wait()
            active -= 1
            return {"job_id": "ignored", "outcome": "success"}

    result = await run_team(
        TeamRequest(question="Research", mode="focused"),
        config=config(tmp_path).model_copy(update={"analyst_concurrency": concurrency}),
        deps=TeamDependencies(role_factory=Role),
    )
    assert maximum == concurrency
    assert result.summary == "6"


@pytest.mark.parametrize(
    "jobs",
    [
        [{"job_id": str(i), "question": "Too many"} for i in range(3)],
        [{"job_id": "duplicate", "question": "One"}, {"job_id": "duplicate", "question": "Two"}],
    ],
)
async def test_invalid_batch_spawns_no_analyst_and_lead_recovers(tmp_path, jobs):
    lead = ScriptModel(
        output("research_batch", jobs=jobs), output("TeamResult", summary="recovered")
    )
    models = deque([lead])
    result = await run_team(
        TeamRequest(question="Investigate", mode="focused"),
        config=config(tmp_path).model_copy(update={"max_batch_jobs": 2}),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.summary == "recovered"
    assert json.loads(Path(result.artifacts["research"]).read_text()) == []
    assert "error" in json.dumps(lead.calls[-1]["messages"])


async def test_all_role_prompts_are_frozen_before_lead_delegates(tmp_path):
    import hashlib
    import shutil

    from wheel_team.config import PromptSet

    bundle = tmp_path / "prompts"
    shutil.copytree(Path("src/wheel_team/prompts/development"), bundle)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["modules"] = {}
    manifest_path.write_text(json.dumps(manifest))
    original = (bundle / "analyst.md").read_text()
    contexts = []

    class Role:
        def __init__(self, role, context):
            self.role, self.context = role, context
            contexts.append((role, context.prompt.content))

        async def invoke_async(self, question):
            if self.role == "team_lead":
                changed = "Changed after run start"
                (bundle / "analyst.md").write_text(changed)
                manifest["roles"]["analyst"]["content_hash"] = hashlib.sha256(
                    changed.encode()
                ).hexdigest()
                manifest_path.write_text(json.dumps(manifest))
                await self.context.tools["research_batch"]([{"job_id": "a", "question": "Check"}])
                return {"summary": "frozen"}
            return {"job_id": "a", "outcome": "success"}

    result = await run_team(
        TeamRequest(question="Investigate", mode="focused"),
        config=config(tmp_path).model_copy(
            update={"prompts": PromptSet(mode="development", manifest_path=manifest_path)}
        ),
        deps=TeamDependencies(role_factory=Role),
    )
    assert result.summary == "frozen"
    assert contexts[1] == ("analyst", original)


async def test_saved_offline_trace_fixture_invokes_actual_tools(tmp_path):
    from tests.support.thin_team_trace import run_fixture

    result = await run_fixture(tmp_path)
    assert result.completion_reason == "complete"
    rows = [json.loads(line) for line in Path(result.artifacts["trace"]).read_text().splitlines()]
    names = [row["name"] for row in rows if row["event"] == "tool_call_started"]
    assert names.count("research_batch") == 1
    assert names.count("consult_expert") == 1
    assert "finance_calculate" in names
    assert "market_query" in names
    assert "knowledge_read" in names
    assert (
        json.loads(Path(result.artifacts["expert"]).read_text())[0]["requested_follow_up"][0][
            "job_id"
        ]
        == "source-time"
    )
    assert list((Path(result.artifacts["result"]).parent / "records").glob("*.json"))


def test_cli_scripted_case_runs_actual_batch_expert_and_records_effective_schemas(tmp_path, capsys):
    from wheel_team.cli import main

    assert (
        main(
            [
                "eval",
                "--case",
                "evals/thin/delegation-script.json",
                "--mode",
                "scripted",
                "--config",
                "evals/thin/delegation-config.json",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )
    response = json.loads(capsys.readouterr().out)
    manifest = json.loads(Path(response["artifacts"]["manifest"]).read_text())
    trace = [
        json.loads(line) for line in Path(response["artifacts"]["trace"]).read_text().splitlines()
    ]
    names = [row["name"] for row in trace if row["event"] == "tool_call_started"]
    assert "research_batch" in names and "consult_expert" in names
    assert "finance_calculate" in names and "market_query" in names
    assert set(manifest["tool_specs"]) == {"team_lead", "analyst", "wheel_expert"}
    assert (
        "jobs"
        in manifest["tool_specs"]["team_lead"]["research_batch"]["inputSchema"]["json"][
            "properties"
        ]
    )
    assert len(manifest["tool_specs_hash"]) == 64
    assert manifest["generations"] == 9
