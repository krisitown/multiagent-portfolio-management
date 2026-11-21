"""Council wiring uses synthetic prompts and the real SDK, without network inference."""

import hashlib
import json
from collections import deque
from pathlib import Path

import pytest

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_analyst_report_access import expert_data, tool_data
from tests.thin.test_direct_text_handoff import CutoffModel, direct_config
from wheel_team.config import PromptSet, TeamConfig
from wheel_team.followup import load_parent_review
from wheel_team.team import run_team
from wheel_team.types import TeamDependencies, TeamRequest


def council_config(tmp_path, **updates):
    root = tmp_path / "prompts"
    root.mkdir(exist_ok=True)
    roles = {}
    for role in [
        "team_lead",
        "analyst",
        "explorer",
        "wheel_expert",
        "dalio",
        "buffett",
        "portfolio_manager",
    ]:
        body = f"SYNTHETIC TEST FIXTURE ONLY: {role}"
        (root / f"{role}.md").write_text(body)
        roles[role] = {
            "path": f"{role}.md",
            "content_hash": hashlib.sha256(body.encode()).hexdigest(),
        }
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "prompt_set_id": "synthetic-council",
                "version": "1",
                "status": "development",
                "roles": roles,
            }
        )
    )
    data = direct_config(tmp_path).model_dump() | {
        "council_enabled": True,
        "prompts": PromptSet(mode="development", manifest_path=manifest).model_dump(),
        **updates,
    }
    return TeamConfig.model_validate(data)


async def test_manager_final_exact_and_specialist_followup_originals(tmp_path):
    final = "  ## Manager original α\nDeliberate recommendation.  "
    manager = ScriptModel(
        output("consult_specialists", requests=[{"role": "dalio", "question": "Initial view"}]),
        output("consult_specialists", requests=[{"role": "dalio", "question": "Follow up"}]),
        {"text": final},
    )
    first, second = (
        ScriptModel({"text": "Dalio initial original"}),
        ScriptModel({"text": "Dalio revised original"}),
    )
    models = deque([manager, first, second])
    result = await run_team(
        TeamRequest(
            question="PRIVATE TASK",
            mode="combined",
            portfolio_text="PRIVATE PORTFOLIO",
            owner_context="PRIVATE OWNER",
        ),
        config=council_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.completion_reason == "complete" and not models
    assert result.report_markdown == result.summary == final
    assert Path(result.artifacts["report"]).read_text() == final
    prior = expert_data(second)["prior_specialist_assessments"]
    assert prior[0]["role"] == "dalio" and prior[0]["assessment"] == "Dalio initial original"
    saved = json.loads(Path(result.artifacts["specialists"]).read_text())
    assert [r["role"] for r in saved] == ["dalio", "dalio"]
    parent = load_parent_review(Path(result.artifacts["report"]).parent)["packet"]
    assert parent["final_author"] == "portfolio_manager"
    assert parent["specialist_assessments"] == saved
    assert parent["prior_report"] == final


async def test_private_peers_public_research_reuse_and_bound_caller(tmp_path):
    manager = ScriptModel(
        output(
            "consult_specialists",
            requests=[
                {"role": "buffett", "question": "Business view"},
                {"role": "wheel_expert", "question": "Income view"},
            ],
        ),
        {"text": "Manager synthesis"},
    )
    buffett = ScriptModel(
        output("request_research", jobs=[{"job_id": "a", "question": "PUBLIC BUSINESS"}]),
        {"text": "Buffett view"},
    )
    analyst = ScriptModel({"text": "Public original"})
    wheel = ScriptModel(
        output("request_research", jobs=[{"job_id": "b", "question": "PUBLIC BUSINESS"}]),
        {"text": "Wheel view"},
    )
    # Distinct peers start together and share the registered research task.
    models = deque([manager, buffett, wheel, analyst])
    contexts = []
    from wheel_team.agents.base import build_role

    def factory(role, ctx):
        contexts.append((role, ctx))
        return build_role(role, ctx)

    result = await run_team(
        TeamRequest(
            question="PRIVATE TASK",
            mode="combined",
            portfolio_text="PRIVATE PORTFOLIO",
            owner_context="PRIVATE OWNER",
        ),
        config=council_config(tmp_path, analyst_concurrency=1),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft(), role_factory=factory),
    )
    assert result.completion_reason == "complete" and not models
    for role, ctx in contexts:
        if role in {"portfolio_manager", "buffett", "wheel_expert"}:
            assert ctx.request.portfolio_text == "PRIVATE PORTFOLIO"
        if role in {"buffett", "wheel_expert"}:
            assert (
                "consult_specialists" not in ctx.tools
                and "consult_portfolio_manager" not in ctx.tools
            )
        if role == "analyst":
            assert ctx.request.portfolio_text is ctx.request.owner_context is None
            assert "PRIVATE" not in json.dumps(ctx.request.model_dump())
    assert tool_data(wheel.calls[1])["request_mapping"][0]["reused"] is True
    requests = json.loads(Path(result.artifacts["expert_research"]).read_text())
    assert [r["author_role"] for r in requests] == ["buffett", "wheel_expert"]


@pytest.mark.parametrize("failure", [RuntimeError("offline"), "partial"])
async def test_failed_or_cutoff_manager_cannot_promote_lead_to_final(tmp_path, failure):
    manager = (
        CutoffModel({"text": "Partial manager original"})
        if failure == "partial"
        else ScriptModel(failure)
    )
    models = deque([manager])
    result = await run_team(
        TeamRequest(question="review", mode="education"),
        config=council_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.completion_reason != "complete"
    assert result.report_markdown != "Lead says all complete"
    if failure == "partial":
        assert "Partial manager original" in result.report_markdown


async def test_manager_is_initial_agent_without_lead(tmp_path):
    roles = []
    from wheel_team.agents.base import build_role

    def factory(role, ctx):
        roles.append(role)
        return build_role(role, ctx)

    result = await run_team(
        TeamRequest(question="review", mode="education"),
        config=council_config(tmp_path),
        deps=TeamDependencies(
            model_factory=lambda *_: ScriptModel({"text": "Manager direct"}), role_factory=factory
        ),
    )
    assert roles == ["portfolio_manager"]
    assert result.report_markdown == "Manager direct"


@pytest.mark.parametrize("role", ["dalio", "buffett", "portfolio_manager"])
async def test_new_private_authors_select_private_checker_originals(tmp_path, role):
    from tests.thin.test_reasoning_check_runtime import session
    from wheel_team.fact_check_runtime import ReasoningCheckSession

    checker, ctx, created = session(tmp_path, [])
    model = ScriptModel({"text": "Scoped substantive feedback"})
    checker = ReasoningCheckSession(
        tmp_path / "new-check",
        ctx,
        lambda: model,
        author_role=role,
        counter=checker.counter,
        private_inputs={
            "input:portfolio": {"body": "PRIVATE ORIGINAL", "metadata": {"scope": "private"}}
        },
    )
    reply = await checker.check("Exact claim", ["input:portfolio"], [])
    assert reply["status"] == "completed"
    packet = json.loads(model.calls[0]["messages"][0]["content"][0]["text"])
    assert packet["author_role"] == role and packet["evidence"][0]["body"] == "PRIVATE ORIGINAL"
    assert not model.calls[0]["tools"]


async def test_manager_checks_original_peer_assessment_and_scope(tmp_path):
    import hashlib

    from wheel_team.config import FactCheckConfig

    cfg = council_config(tmp_path)
    root = tmp_path / "checker"
    root.mkdir()
    body = "SYNTHETIC CHECKER"
    (root / "checker.md").write_text(body)
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "prompt_set_id": "test-checker",
                "version": "1",
                "status": "development",
                "roles": {
                    "fact_checker": {
                        "path": "checker.md",
                        "content_hash": hashlib.sha256(body.encode()).hexdigest(),
                    }
                },
            }
        )
    )
    cfg = cfg.model_copy(
        update={
            "fact_check": FactCheckConfig(
                enabled=True, prompts=PromptSet(mode="development", manifest_path=manifest)
            )
        }
    )
    manager = ScriptModel(
        output("consult_specialists", requests=[{"role": "buffett", "question": "View"}]),
        output(
            "fact_check",
            message="Material synthesis",
            evidence_refs=["input:specialist:1", "input:portfolio"],
        ),
        {"text": "Checked PM original"},
    )
    peer = ScriptModel({"text": "Original Buffett assessment"})
    checker = ScriptModel({"text": "Substantive scoped feedback"})
    models = deque([manager, peer, checker])
    result = await run_team(
        TeamRequest(question="Task", mode="combined", portfolio_text="Private account original"),
        config=cfg,
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.report_markdown == "Checked PM original"
    packet = json.loads(checker.calls[0]["messages"][0]["content"][0]["text"])
    assert packet["evidence"][0]["body"]["role"] == "buffett"
    assert packet["evidence"][0]["body"]["result"]["assessment"] == "Original Buffett assessment"


async def test_existing_scenario_calculator_is_available_to_private_council_only(tmp_path):
    from wheel_team.agents.base import role_tools

    cfg = council_config(tmp_path)
    for role in ["portfolio_manager", "dalio", "buffett", "wheel_expert"]:
        assert "option_scenarios" in role_tools(role, cfg)
    for role in ["team_lead", "analyst", "explorer"]:
        assert "option_scenarios" not in role_tools(role, cfg)


async def test_cancelled_peer_retains_status_and_cannot_be_completed_report(tmp_path):
    import asyncio

    manager = ScriptModel(
        output("consult_specialists", requests=[{"role": "dalio", "question": "Macro view"}]),
        {"text": "Unreachable"},
    )
    waiting = asyncio.Event()
    peer = ScriptModel(waiting)
    models = deque([manager, peer])
    task = asyncio.create_task(
        run_team(
            TeamRequest(question="Task", mode="combined"),
            config=council_config(tmp_path),
            deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
        )
    )
    for _ in range(100):
        if peer.calls:
            break
        await asyncio.sleep(0.01)
    assert peer.calls
    task.cancel()
    result = await task
    assert result.completion_reason == "cancelled"
    rows = json.loads(Path(result.artifacts["specialists"]).read_text())
    assert rows[0]["outcome"] == "cancelled" and rows[0]["result"]["error"]


async def test_manager_continuity_read_is_attributed_and_read_only(tmp_path):
    from tests.thin.test_fact_check_team import Capture
    from wheel_team.config import ContinuityConfig

    cfg = council_config(tmp_path).model_copy(
        update={
            "continuity": ContinuityConfig(
                root=tmp_path / "memory", prompts=PromptSet.development()
            )
        }
    )
    manager = Capture(
        output("consult_continuity", message="Prior owner objectives"),
        {"text": "PM original final"},
    )
    retrieval = Capture({"text": "Attributed original memory brief"})
    models = deque([manager, retrieval])
    result = await run_team(
        TeamRequest(question="Owner objective", mode="combined"),
        config=cfg,
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.report_markdown == "PM original final" and not models
    schema = next(t for t in manager.calls[0]["tools"] if t["name"] == "consult_continuity")[
        "inputSchema"
    ]["json"]
    assert schema["properties"]["access"]["const"] == "read"
    response = tool_data(manager.calls[1])
    assert response["text"] == "Attributed original memory brief"
    assert "read_refs" not in response and "writes" not in response


async def test_completed_manager_needs_no_lead_synthesis(tmp_path):
    manager = ScriptModel({"text": "Completed authoritative PM original"})
    models = deque([manager])
    result = await run_team(
        TeamRequest(question="review", mode="combined"),
        config=council_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.completion_reason == "complete" and not models
    assert result.report_markdown == "Completed authoritative PM original"


async def test_manager_receives_peer_requested_original_research(tmp_path):
    manager = ScriptModel(
        output("consult_specialists", requests=[{"role": "buffett", "question": "Business view"}]),
        output("evidence_read", record_ref="input:analyst:1"),
        {"text": "PM final"},
    )
    peer = ScriptModel(
        output(
            "request_research", jobs=[{"job_id": "peer-job", "question": "Public filing detail"}]
        ),
        {"text": "Peer interpretation"},
    )
    analyst = ScriptModel({"text": "Exact original analyst text α"})
    models = deque([manager, peer, analyst])
    result = await run_team(
        TeamRequest(question="review", mode="combined"),
        config=council_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    handoff = tool_data(manager.calls[1])
    assert "analyst_reports" not in handoff
    packet = tool_data(manager.calls[2])
    original = json.loads(Path(result.artifacts["research"]).read_text())[0]
    assert packet["record"] == original
    assert packet["record"]["summary"] == "Exact original analyst text α"


async def test_manager_cannot_recursively_invoke_manager(tmp_path):
    from wheel_team.agents.base import role_tools

    assert "consult_portfolio_manager" not in role_tools(
        "portfolio_manager", council_config(tmp_path)
    )


@pytest.mark.parametrize("terminal", ["cancelled", "deadline"])
async def test_failed_manager_preserves_actual_terminal_cause(tmp_path, terminal):
    import asyncio

    manager = ScriptModel(asyncio.Event())
    models = deque([manager])
    cfg = council_config(tmp_path, deadline_seconds=0.1 if terminal == "deadline" else 30)
    task = asyncio.create_task(
        run_team(
            TeamRequest(question="review", mode="combined"),
            config=cfg,
            deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
        )
    )
    if terminal == "cancelled":
        for _ in range(100):
            if manager.calls:
                break
            await asyncio.sleep(0.01)
        assert manager.calls
        task.cancel()
    result = await task
    assert result.completion_reason == terminal
    assert result.report_markdown != "Lead success"


async def test_record_only_council_archives_without_lead_or_manager(tmp_path):
    from wheel_team.config import ContinuityConfig
    from wheel_team.owner_feedback import run_feedback

    cfg = council_config(tmp_path).model_copy(
        update={
            "continuity": ContinuityConfig(
                root=tmp_path / "memory", prompts=PromptSet.development()
            )
        }
    )
    memory = ScriptModel(
        output(
            "continuity_write",
            path="vault/owner-goals.md",
            content="Attributed owner objective; expected funding not settled.",
            expected_revision=None,
        ),
        {"text": "Saved original owner goals"},
    )
    models = deque([memory])
    receipt = await run_feedback(
        {
            "action": "record",
            "text": "Exact owner objective",
            "transport_key": "record-only-council",
        },
        config=cfg,
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert receipt["status"] == "completed" and not receipt.get("archive_error")
    assert receipt["notes_written"] and receipt.get("discussion_ref")
    archived = (tmp_path / "memory" / receipt["discussion_ref"]).read_text()
    assert "Recording-only acknowledgment" in archived
    assert "no investment assessment" in archived


async def test_runtime_budget_names_manager_limit_without_claiming_peer_exhaustion(tmp_path):
    from tests.thin.test_fact_check_team import Capture

    manager = Capture({"text": "Manager final"})
    models = deque([manager])
    await run_team(
        TeamRequest(question="review", mode="combined"),
        config=council_config(tmp_path, max_expert_consultations=1),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert '"manager_consultations_remaining": 0' in manager.system
    assert '"expert_consultations_remaining"' not in manager.system


@pytest.mark.parametrize("council", [False, True])
async def test_asymmetric_uses_shared_council_budget_preserving_legacy_cap(tmp_path, council):
    lead = ScriptModel(
        output("consult_asymmetric", question="Public opportunity task"),
        output("consult_portfolio_manager") if council else {"text": "Legacy coordination"},
        *([{"text": "Coordination"}] if council else []),
    )
    asymmetric = ScriptModel(
        *([output("evidence_find", kind="evidence", query={})] * 32),
        {"text": "Original after thirty-two public calls"},
    )
    manager = ScriptModel({"text": "PM final"})
    if council:
        manager = ScriptModel(
            output("consult_asymmetric", question="Public opportunity task"), {"text": "PM final"}
        )
    models = deque([manager, asymmetric] if council else [lead, asymmetric])
    cfg = (
        council_config(tmp_path, asymmetric_enabled=True, deadline_seconds=18000)
        if council
        else direct_config(tmp_path).model_copy(
            update={"asymmetric_enabled": True, "deadline_seconds": 18000}
        )
    )
    result = await run_team(
        TeamRequest(question="Task", mode="combined"),
        config=cfg,
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    row = json.loads(Path(result.artifacts["asymmetric"]).read_text())[0]
    assert row["outcome"] == ("success" if council else "failed")
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert manifest["asymmetric_opportunities"]["deadline_seconds"] == (18000 if council else 2700)
    if council:
        assert row["summary"] == "Original after thirty-two public calls"


async def test_council_asymmetric_cannot_reset_shared_generation_ceiling(tmp_path):
    lead = ScriptModel(
        output("consult_asymmetric", question="Public task"), {"text": "Unreachable"}
    )
    asymmetric = ScriptModel(
        *([output("evidence_find", kind="evidence", query={})] * 5), {"text": "Unreachable"}
    )
    models = deque([lead, asymmetric])
    result = await run_team(
        TeamRequest(question="Task", mode="combined"),
        config=council_config(tmp_path, asymmetric_enabled=True, max_generations=3),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert manifest["generations"] == 3
    row = json.loads(Path(result.artifacts["asymmetric"]).read_text())[0]
    assert row["outcome"] == "budget_exhausted" and row["error"]


async def test_council_asymmetric_cancellation_preserves_incomplete_original(tmp_path):
    import asyncio

    lead = ScriptModel(
        output("consult_asymmetric", question="Public task"), {"text": "Unreachable"}
    )
    asymmetric = ScriptModel(asyncio.Event())
    models = deque([lead, asymmetric])
    task = asyncio.create_task(
        run_team(
            TeamRequest(question="Task", mode="combined"),
            config=council_config(tmp_path, asymmetric_enabled=True),
            deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
        )
    )
    for _ in range(100):
        if asymmetric.calls:
            break
        await asyncio.sleep(0.01)
    assert asymmetric.calls
    task.cancel()
    result = await task
    assert result.completion_reason == "cancelled"
    row = json.loads(Path(result.artifacts["asymmetric"]).read_text())[0]
    assert row["outcome"] == "cancelled" and row["error"]
