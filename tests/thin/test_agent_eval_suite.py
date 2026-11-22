import asyncio
import json
from datetime import UTC, datetime

import pytest

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_role_isolation import model_config
from portfolio_management.evaluation.agent_suite import run_agent_case, validate_suite
from portfolio_management.evaluation.thin_runner import mock_tools_from_specs
from portfolio_management.types import TraceSink


@pytest.fixture
def spec():
    return {
        "name": "fact_check",
        "description": "Current checker contract",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "evidence_refs": {"type": "array", "items": {"type": "string"}},
                    "assumptions": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["message", "evidence_refs", "assumptions"],
            }
        },
    }


async def test_exact_schema_and_semantic_arguments_not_call_order(tmp_path, spec):
    trace = TraceSink(tmp_path / "trace.jsonl", lambda: datetime.now(UTC))
    tools = mock_tools_from_specs(
        {"fact_check": spec},
        {
            "fact_check": {
                "routes": [
                    {
                        "contains_all": ["wrong", "delta"],
                        "response": {"feedback": "Repair delta sign"},
                    }
                ],
                "default": {"feedback": "No demonstrated conflict in supplied scope"},
            }
        },
        trace,
    )
    assert tools["fact_check"].tool_spec == spec
    assert (
        await tools["fact_check"](
            message="The wrong position delta", evidence_refs=[], assumptions=[]
        )
    )["feedback"] == "Repair delta sign"
    assert (
        "No demonstrated"
        in (await tools["fact_check"](message="Correct", evidence_refs=[], assumptions=[]))[
            "feedback"
        ]
    )
    rows = [json.loads(x) for x in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert rows[0]["arguments"]["message"] == "The wrong position delta"
    assert all("result" in r for r in rows)


async def test_delegated_jobs_echo_actual_ids_and_unmatched_are_coverage(tmp_path):
    trace = TraceSink(tmp_path / "trace.jsonl", lambda: datetime.now(UTC))
    spec = {
        "name": "request_research",
        "description": "Research",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {"jobs": {"type": "array", "items": {"type": "object"}}},
                "required": ["jobs"],
            }
        },
    }
    tools = mock_tools_from_specs(
        {"request_research": spec},
        {
            "request_research": {
                "reports_by_topic": [
                    {
                        "contains_all": ["aster"],
                        "response": {
                            "outcome": "success",
                            "summary": "Fictional Aster evidence",
                            "evidence_refs": ["evidence:aster"],
                        },
                    }
                ]
            }
        },
        trace,
    )
    r = await tools["request_research"](
        jobs=[{"job_id": "chosen-id", "question": "Research Aster cash flow", "evidence_refs": []}]
    )
    assert r["reports"][0]["job_id"] == "chosen-id"
    assert r["reports"][0]["summary"] == "Fictional Aster evidence"
    r = await tools["request_research"](
        jobs=[{"job_id": "unknown", "question": "Different world", "evidence_refs": []}]
    )
    assert r["reports"][0]["outcome"] == "partial"
    assert "mock_coverage" in (tmp_path / "trace.jsonl").read_text()


async def test_native_role_direct_text_and_factcheck_mock_no_real_tool(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("Network forbidden"))
    cfg = model_config(tmp_path).model_copy(update={"child_output_mode": "direct_text"})
    # The SDK receives ordinary final output, not a structured output repair call.
    model = ScriptModel({"text": "Ordinary simulated role response"})
    case = {
        "case_id": "A1",
        "role": "analyst",
        "simulation": True,
        "question": "Use supplied fictional evidence only.",
        "tool_fixtures": {},
    }
    r = await run_agent_case(case, cfg, {}, tmp_path / "case", model=model)
    assert r["ordinary_output"] == "Ordinary simulated role response"
    assert r["status"] == "completed" and r["simulation"] is True
    assert r["model_generations"] == 1
    assert not r["grading"]


@pytest.mark.parametrize("role", ["fact_checker", "hermes_presentation"])
async def test_zero_tool_seams_are_labeled_and_do_not_send(tmp_path, role):
    cfg = model_config(tmp_path)
    case = {
        "case_id": "Z1",
        "role": role,
        "simulation": True,
        "question": "Fictional packet",
        "system_prompt": "Frozen authored prompt",
        "tool_fixtures": {},
    }
    r = await run_agent_case(
        case, cfg, {}, tmp_path / "case", model=ScriptModel({"text": "Ordinary feedback"})
    )
    assert r["tool_specs"] == {}
    assert r["real_Hermes_process"] is False
    assert r["ordinary_output"] == "Ordinary feedback"


def test_suite_validation_rejects_gold_and_duplicate_case_ids():
    case = {
        "case_id": "X",
        "role": "analyst",
        "simulation": True,
        "question": "Synthetic facts",
        "tool_fixtures": {},
    }
    with pytest.raises(ValueError, match="grading"):
        validate_suite([case | {"expected_facts": ["secret"]}])
    with pytest.raises(ValueError, match="Duplicate"):
        validate_suite([case, case])


async def test_timeout_preserves_attempt_and_new_directory_required(tmp_path):
    cfg = model_config(tmp_path).model_copy(update={"deadline_seconds": 0.01})

    class SlowModel(ScriptModel):
        async def stream(self, *a, **kw):
            await asyncio.sleep(1)
            async for e in super().stream(*a, **kw):
                yield e

    case = {
        "case_id": "T1",
        "role": "analyst",
        "simulation": True,
        "question": "Synthetic",
        "tool_fixtures": {},
    }
    r = await run_agent_case(
        case, cfg, {}, tmp_path / "case", model=SlowModel({"text": "Too late"})
    )
    assert r["status"] == "timeout" and r["attempted"] is True
    with pytest.raises(FileExistsError):
        await run_agent_case(case, cfg, {}, tmp_path / "case", model=ScriptModel())


def test_cli_dry_run_selects_role_without_model_transport(tmp_path, monkeypatch):
    from portfolio_management.evaluation import agent_suite

    monkeypatch.setattr(
        agent_suite, "build_model", lambda *a, **k: pytest.fail("No model in dry run")
    )
    assert (
        agent_suite.main(
            [
                "--suite",
                "evals/agent_v1",
                "--role",
                "fact_checker",
                "--output",
                str(tmp_path / "dry"),
                "--dry-run",
            ]
        )
        == 0
    )
    r = json.loads((tmp_path / "dry/summary.json").read_text())
    assert r["selected"] == 4 and r["attempted"] == 0
    assert all(x["status"] == "dry_run" for x in r["cases"])


async def test_batch_defers_before_cron_without_counting_failure(tmp_path):
    from portfolio_management.evaluation.agent_suite import run_batch

    cfg = model_config(tmp_path)
    case = {
        "case_id": "D1",
        "role": "analyst",
        "simulation": True,
        "question": "Fictional evidence",
        "tool_fixtures": {},
    }
    r = await run_batch(
        [case], cfg, {}, tmp_path / "batch", stop_before=datetime(2000, 1, 1, tzinfo=UTC)
    )
    assert r["attempted"] == 0 and r["deferred"] == 1
    assert r["cases"][0]["status"] == "deferred_schedule"


async def test_hermes_events_keep_simulated_role_label(tmp_path):
    cfg = model_config(tmp_path)
    c = {
        "case_id": "H1",
        "role": "hermes_presentation",
        "simulation": True,
        "question": "Synthetic",
        "system_prompt": "Frozen editorial instructions",
        "tool_fixtures": {},
    }
    r = await run_agent_case(c, cfg, {}, tmp_path / "case", model=ScriptModel({"text": "Brief"}))
    assert r["model_events"][0]["role"] == "hermes_presentation"


async def test_setup_failure_is_not_a_model_attempt(tmp_path):
    cfg = model_config(tmp_path)
    c = {
        "case_id": "S1",
        "role": "hermes_presentation",
        "simulation": True,
        "question": "Synthetic",
        "tool_fixtures": {},
    }
    r = await run_agent_case(c, cfg, {}, tmp_path / "case", model=ScriptModel())
    assert r["status"] == "harness_error" and r["attempted"] is False
    assert r["trace_clock"] == "scenario_time" and r["started_at"]


async def test_current_deployed_factcheck_schema_runs_in_sdk_loop(tmp_path):
    from pathlib import Path

    specs = json.loads(Path("evals/agent_v1/tool-specs.json").read_text())
    cfg = model_config(tmp_path)
    cfg = cfg.model_copy(
        update={
            "child_output_mode": "direct_text",
            "fact_check": cfg.fact_check.model_copy(update={"enabled": True}),
        }
    )
    case = {
        "case_id": "actual-schema",
        "role": "analyst",
        "simulation": True,
        "question": "Synthetic",
        "tool_fixtures": {
            "fact_check": {
                "default": {
                    "status": "completed",
                    "feedback": "Scoped synthetic feedback",
                    "error": None,
                }
            }
        },
    }
    model = ScriptModel(
        output("fact_check", message="Scoped claim", evidence_refs=[], assumptions=[]),
        {"text": "Checked ordinary response"},
    )
    r = await run_agent_case(case, cfg, specs, tmp_path / "case", model=model)
    assert r["status"] == "completed" and r["model_generations"] == 2
    assert r["tool_specs"] == specs["analyst"]
    assert r["tool_trace"][0]["arguments"]["message"] == "Scoped claim"
    assert r["tool_trace"][0]["result"]["feedback"] == "Scoped synthetic feedback"
    assert r["model_events"][0]["wall_at"] != r["model_events"][0]["at"]


async def test_calculator_fixture_limit_is_coverage_not_factual_failure(tmp_path):
    from pathlib import Path

    from portfolio_management.evaluation.agent_suite import load_suite

    cases, cfg, specs, _ = load_suite(Path("evals/agent_v1"))
    trace = TraceSink(tmp_path / "trace.jsonl", lambda: datetime.now(UTC))
    c = next(c for c in cases if c["role"] == "analyst")
    tools = mock_tools_from_specs(specs["analyst"], c["tool_fixtures"], trace)
    r = await tools["finance_calculate"](
        formula="arithmetic", inputs={"values": ["2", "100"]}, parameters={"operation": "multiply"}
    )
    assert r["status"] == "unavailable"
    events = [json.loads(x) for x in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert events[0]["event"] == "mock_coverage"


@pytest.mark.parametrize(
    "role,name,arguments",
    [
        ("explorer", "options_liquidity", {"symbol": "ASTF"}),
        ("explorer", "public_search", {"query": "Aster"}),
        ("explorer", "market_statistics", {"series": ["ASTF"]}),
        ("analyst", "evidence_read", {"record_ref": "evidence:aster-quote"}),
        ("analyst", "evidence_find", {"kind": "evidence"}),
        (
            "analyst",
            "finance_calculate",
            {"formula": "arithmetic", "inputs": {"values": ["2", "100"]}},
        ),
    ],
)
async def test_sdk_optional_none_defaults_do_not_invalidate_omitted_arguments(
    tmp_path, role, name, arguments
):
    from pathlib import Path

    specs = json.loads(Path("evals/agent_v1/tool-specs.json").read_text())
    cfg = model_config(tmp_path).model_copy(update={"child_output_mode": "direct_text"})
    c = {
        "case_id": "optional-default",
        "role": role,
        "simulation": True,
        "question": "Synthetic ASTF",
        "tool_fixtures": {name: {"default": {"status": "success", "data": {"symbol": "ASTF"}}}},
    }
    model = ScriptModel(output(name, **arguments), {"text": "Controlled options observed"})
    r = await run_agent_case(c, cfg, specs, tmp_path / "case", model=model)
    assert r["status"] == "completed"
    assert len(r["tool_trace"]) == 1 and r["tool_trace"][0]["result"]["status"] == "success"
    assert not r["mock_coverage_events"]


async def test_explicit_invalid_null_and_required_fields_are_not_relaxed(tmp_path):
    from pathlib import Path

    specs = json.loads(Path("evals/agent_v1/tool-specs.json").read_text())
    tools = mock_tools_from_specs(
        specs["explorer"], {}, TraceSink(tmp_path / "trace.jsonl", lambda: datetime.now(UTC))
    )
    with pytest.raises(ValueError, match="production schema"):
        await tools["options_liquidity"](symbol="ASTF", expiries=None)
    with pytest.raises(ValueError, match="production schema"):
        await tools["options_liquidity"](expiries=[])
