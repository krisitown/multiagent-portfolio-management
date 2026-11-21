"""Lead guidance projects SDK advertisement without accepting malformed native output."""

import copy
import hashlib
import json
import os
from collections import deque
from pathlib import Path

import pytest
from pydantic import ValidationError
from strands.models.openai import OpenAIModel
from strands.tools.structured_output._structured_output_context import StructuredOutputContext
from strands.tools.structured_output.structured_output_tool import StructuredOutputTool

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_agents import context
from tests.thin.test_entrypoint import config
from wheel_team.agents.base import build_role, project_team_result_spec
from wheel_team.config import FinalizationConfig
from wheel_team.models import invocation_failure
from wheel_team.team import run_team
from wheel_team.types import ExpertResult, ResearchResult, TeamDependencies, TeamRequest, TeamResult


def assert_array_guidance(text):
    assert "JSON array of objects" in text
    assert "directly" in text
    assert "encoded string" in text
    assert "null" in text
    assert "omit" in text.lower()
    assert "[]" in text


def formatter():
    # Format locally without constructing a client or making an inference request.
    model = OpenAIModel.__new__(OpenAIModel)
    model.config = {"model_id": "offline-format-only"}
    return model


async def test_lead_actual_sdk_and_openai_parameters_are_projected_without_native_mutation(
    tmp_path,
):
    public = copy.deepcopy(TeamResult.model_json_schema())
    native = copy.deepcopy(StructuredOutputTool(TeamResult).tool_spec)
    model = ScriptModel(output("TeamResult", summary="Completed", candidate_slots=[]))
    ctx = context(tmp_path, "team_lead", model)
    prefix = ctx.prompt.content
    agent = build_role("team_lead", ctx)
    response = await agent.invoke_async("Inspect", structured_output_model=TeamResult)
    assert response.structured_output.candidate_slots == ()
    spec = next(s for s in model.calls[0]["tools"] if s["name"] == "TeamResult")
    schema = spec["inputSchema"]["json"]
    assert "report_markdown" not in schema["properties"]
    assert "report_markdown" not in schema.get("required", [])
    slots = schema["properties"]["candidate_slots"]
    assert slots["type"] == "array"
    assert slots["items"] == native["inputSchema"]["json"]["properties"]["candidate_slots"]["items"]
    assert slots["items"]["type"] == "object"
    assert_array_guidance(slots["description"])
    request = formatter().format_request([], model.calls[0]["tools"])
    assert (
        next(
            t["function"]["parameters"]
            for t in request["tools"]
            if t["function"]["name"] == "TeamResult"
        )
        == schema
    )
    assert agent.system_prompt.startswith(prefix + "\n\nRun context (data):\n")
    contract = json.loads(agent.system_prompt.split("Run context (data):\n")[1])["output_contract"]
    assert_array_guidance(contract["field_guidance"]["candidate_slots"])
    assert StructuredOutputTool(TeamResult).tool_spec == native
    assert TeamResult.model_json_schema() == public
    assert "report_markdown" in public["properties"]
    assert native["inputSchema"]["json"]["properties"]["candidate_slots"]["type"] == [
        "array",
        "null",
    ]
    # A consumer modifying its detached advertisement cannot mutate future SDK tools.
    slots["items"]["type"] = "string"
    assert StructuredOutputTool(TeamResult).tool_spec == native


def test_projection_detaches_nested_schema_and_removes_host_required_entry():
    native = StructuredOutputTool(TeamResult).tool_spec
    native["inputSchema"]["json"]["required"].append("report_markdown")
    before = copy.deepcopy(native)
    projected = project_team_result_spec(native)
    assert projected["name"] == "TeamResult"
    assert projected["inputSchema"]["json"]["required"] == ["summary"]
    projected["inputSchema"]["json"]["properties"]["candidate_slots"]["items"]["type"] = "string"
    assert native == before
    assert "report_markdown" in native["inputSchema"]["json"]["properties"]


@pytest.mark.parametrize(
    "role,output_type,values",
    [
        ("analyst", ResearchResult, {"job_id": "a", "outcome": "success"}),
        ("wheel_expert", ExpertResult, {"assessment": "Completed"}),
    ],
)
async def test_other_role_output_advertisements_remain_native(tmp_path, role, output_type, values):
    model = ScriptModel(output(output_type.__name__, **values))
    agent = build_role(role, context(tmp_path, role, model))
    await agent.invoke_async("Inspect", structured_output_model=output_type)
    native = copy.deepcopy(StructuredOutputTool(output_type).tool_spec)
    agent.tool_registry.validate_tool_spec(native)
    assert model.calls[0]["tools"] == [native]
    contract = json.loads(agent.system_prompt.split("Run context (data):\n")[1])["output_contract"]
    assert "field_guidance" not in contract


async def test_manifest_records_actual_projected_lead_advertisement(tmp_path):
    model = ScriptModel(output("TeamResult", summary="Completed"))
    result = await run_team(
        TeamRequest(question="Inspect", mode="education"),
        config=config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: model),
    )
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    actual = {s["name"]: s for s in model.calls[0]["tools"]}
    assert manifest["tool_specs"]["team_lead"] == actual
    schema = actual["TeamResult"]["inputSchema"]["json"]
    assert "report_markdown" not in schema["properties"]
    assert schema["properties"]["candidate_slots"]["type"] == "array"
    assert (
        manifest["tool_specs_hash"]
        == hashlib.sha256(
            json.dumps(manifest["tool_specs"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


@pytest.mark.parametrize("bad", [None, "[]", '[{"slot": 1}]}', {}, 7, False])
async def test_wrong_container_feedback_keeps_native_error_raw_args_and_one_repair(tmp_path, bad):
    proposal = {"summary": "Rejected", "candidate_slots": bad}
    model = ScriptModel(
        output("TeamResult", **proposal),
        output("TeamResult", summary="A separate corrected proposal", candidate_slots=[]),
    )
    ctx = context(tmp_path, "team_lead", model)
    ctx.config = ctx.config.model_copy(
        update={"finalization": FinalizationConfig(max_output_repairs=1)}
    )
    result = await build_role("team_lead", ctx).invoke_async(
        "Inspect", structured_output_model=TeamResult
    )
    assert result.structured_output.summary == "A separate corrected proposal"
    assert ctx.budget.generations == 2
    feedback = [
        b["toolResult"]
        for m in model.calls[1]["messages"]
        for b in m["content"]
        if "toolResult" in b
    ][0]
    assert feedback["status"] == "error"
    assert feedback["content"][0]["text"] == (
        "Validation failed for TeamResult. Please fix the following errors:\n"
        "- Field 'candidate_slots': Input should be a valid tuple"
    )
    assert_array_guidance("\n".join(c.get("text", "") for c in feedback["content"][1:]))
    trace = [json.loads(line) for line in ctx.trace.path.read_text().splitlines()]
    started = [r for r in trace if r["event"] == "tool_call_started"]
    finished = [r for r in trace if r["event"] == "tool_call_finished"]
    assert started[0]["inputs"] == proposal
    assert finished[0]["status"] == "error" and finished[0]["result"] is None


@pytest.mark.parametrize(
    "slots,accepted",
    [([], True), ([{"slot": 1}], True), (None, False), ("[]", False), (["not an object"], False)],
)
async def test_native_sdk_validation_is_strict_and_only_stores_valid_arrays(slots, accepted):
    native = StructuredOutputTool(TeamResult)
    state = StructuredOutputContext(TeamResult)
    raw = {"summary": "Native validation", "candidate_slots": slots}
    before = copy.deepcopy(raw)
    events = [
        e
        async for e in native.stream(
            {"name": "TeamResult", "toolUseId": "native-call", "input": raw},
            {},
            structured_output_context=state,
        )
    ]
    assert events[-1]["tool_result"]["status"] == ("success" if accepted else "error")
    assert (state.get_result("native-call") is not None) is accepted
    assert raw == before


async def test_invalid_array_items_do_not_receive_wrong_container_hint(tmp_path):
    model = ScriptModel(
        output("TeamResult", summary="Rejected item", candidate_slots=["not an object"]),
        output("TeamResult", summary="Corrected", candidate_slots=[]),
    )
    ctx = context(tmp_path, "team_lead", model)
    ctx.config = ctx.config.model_copy(
        update={"finalization": FinalizationConfig(max_output_repairs=1)}
    )
    await build_role("team_lead", ctx).invoke_async("Inspect", structured_output_model=TeamResult)
    feedback = [
        b["toolResult"]
        for m in model.calls[1]["messages"]
        for b in m["content"]
        if "toolResult" in b
    ][0]
    assert feedback["status"] == "error"
    assert len(feedback["content"]) == 1
    assert "candidate_slots -> 0" in feedback["content"][0]["text"]
    assert "valid dictionary" in feedback["content"][0]["text"]


async def test_wrong_container_feedback_cannot_restart_repair_allowance(tmp_path):
    model = ScriptModel(
        output("TeamResult", summary="First rejection", candidate_slots="[]"),
        output("TeamResult", summary="Second rejection", candidate_slots=None),
        output("TeamResult", summary="Must never be accepted", candidate_slots=[]),
    )
    ctx = context(tmp_path, "team_lead", model)
    ctx.config = ctx.config.model_copy(
        update={"finalization": FinalizationConfig(max_output_repairs=1)}
    )
    with pytest.raises(Exception) as caught:
        await build_role("team_lead", ctx).invoke_async(
            "Inspect", structured_output_model=TeamResult
        )
    assert invocation_failure(caught.value) == "output_repairs"
    assert ctx.budget.generations == len(model.calls) == 2


async def test_exact_private_failure_remains_native_rejection_with_canonical_feedback(tmp_path):
    selected = os.environ.get("WHEEL_TEST_LEAD_FAILURE_TRACE")
    if not selected:
        pytest.skip("Select observed private failure with WHEEL_TEST_LEAD_FAILURE_TRACE")
    trace_path = Path(selected)
    original = trace_path.read_bytes()
    rows = [json.loads(line) for line in original.splitlines()]
    failures = [
        r for r in rows if r["event"] == "tool_call_started" and r.get("name") == "TeamResult"
    ]
    assert len(failures) == 1
    proposal = failures[0]["inputs"]
    encoded = proposal["candidate_slots"]
    assert isinstance(encoded, str) and len(encoded) == 921
    assert (
        hashlib.sha256(encoded.encode()).hexdigest()
        == "c26af36a2b5f208431e1d0f93eda7385871c185d402f58e95eaf6ee2f85efc6d"
    )
    with pytest.raises(json.JSONDecodeError) as malformed:
        json.loads(encoded)
    assert malformed.value.msg == "Extra data" and malformed.value.pos == 920
    assert encoded[malformed.value.pos :] == "}"
    with pytest.raises(ValidationError) as invalid:
        TeamResult.model_validate(proposal)
    assert [(e["loc"], e["type"]) for e in invalid.value.errors()] == [
        (("candidate_slots",), "tuple_type")
    ]
    state = StructuredOutputContext(TeamResult)
    events = [
        e
        async for e in StructuredOutputTool(TeamResult).stream(
            {"name": "TeamResult", "toolUseId": "observed", "input": proposal},
            {},
            structured_output_context=state,
        )
    ]
    assert events[-1]["tool_result"]["status"] == "error"
    assert state.results == {}
    model = ScriptModel(output("TeamResult", **proposal), output("TeamResult", **proposal))
    ctx = context(tmp_path, "team_lead", model)
    ctx.config = ctx.config.model_copy(
        update={"finalization": FinalizationConfig(max_output_repairs=1)}
    )
    with pytest.raises(Exception) as caught:
        await build_role("team_lead", ctx).invoke_async(
            "Replay validation only", structured_output_model=TeamResult
        )
    assert invocation_failure(caught.value) == "output_repairs"
    assert ctx.budget.generations == len(model.calls) == 2
    feedback = [
        b["toolResult"]
        for m in model.calls[1]["messages"]
        for b in m["content"]
        if "toolResult" in b
    ][0]
    assert feedback["content"][:1] == events[-1]["tool_result"]["content"]
    assert_array_guidance("\n".join(c.get("text", "") for c in feedback["content"][1:]))
    saved = [json.loads(line) for line in ctx.trace.path.read_text().splitlines()]
    assert all(r["inputs"] == proposal for r in saved if r["event"] == "tool_call_started")
    assert all(r["status"] == "error" for r in saved if r["event"] == "tool_call_finished")
    assert trace_path.read_bytes() == original


async def test_host_fallback_keeps_completed_child_prose_without_false_lead_attribution(tmp_path):
    analyst_prose = "Analyst exact prose.\n\n  Spacing and **source uncertainty** remain."
    expert_prose = "Expert exact prose.\n\nNo accepted lead synthesis follows."
    lead = ScriptModel(
        output("research_batch", jobs=[{"job_id": "a", "question": "Inspect"}]),
        output("consult_expert", question="Review saved work", idea_refs=[]),
        output("TeamResult", summary="FAILED LEAD PROSE MUST NOT BE SAVED", candidate_slots="[]"),
    )
    scripts = deque(
        [
            lead,
            ScriptModel(
                output(
                    "ResearchResult",
                    job_id="a",
                    outcome="partial",
                    summary=analyst_prose,
                    findings=[
                        {
                            "code": "unit_source_gap",
                            "severity": "error",
                            "reason": "Synthetic source gap; no issuer facts.",
                        }
                    ],
                )
            ),
            ScriptModel(output("ExpertResult", assessment=expert_prose)),
        ]
    )
    cfg = config(tmp_path).model_copy(
        update={"finalization": FinalizationConfig(max_output_repairs=0)}
    )
    result = await run_team(
        TeamRequest(question="Inspect", mode="focused"),
        config=cfg,
        deps=TeamDependencies(model_factory=lambda *_: scripts.popleft()),
    )
    assert result.completion_reason == "partial"
    assert result.candidate_slots == ()
    report = Path(result.artifacts["report"]).read_text()
    saved = json.loads(Path(result.artifacts["result"]).read_text())
    assert analyst_prose in result.summary and expert_prose in result.summary
    assert analyst_prose in saved["summary"] and expert_prose in saved["summary"]
    assert analyst_prose in report and expert_prose in report
    assert "Run stopped before final synthesis." in report
    assert "a completed team investment judgment is not implied" in report
    assert "Lead-provided synthesis" not in report
    assert "Saved content below is subject to these findings." not in report
    assert "FAILED LEAD PROSE" not in report
    assert "output_repairs" in report
