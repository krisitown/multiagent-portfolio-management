"""Invocation limits protect finalization without forcing a research trajectory."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from strands import Agent, tool

from tests.support.thin_fakes import fixed_clock
from tests.support.thin_sdk import ScriptModel, output
from wheel_team import models
from wheel_team.types import ExpertResult, ResearchResult, RunBudget, TeamResult, TraceSink


def budget():
    return RunBudget(20, 5, fixed_clock() + timedelta(minutes=45), asyncio.Semaphore(1))


def hooked_agent(tmp_path, model, role="analyst", shared=None, tools=(), **options):
    shared = shared or budget()
    trace = TraceSink(tmp_path / "trace.jsonl", fixed_clock)
    hooks = models.RunHooks(shared, trace, role, role, **options)
    return (
        Agent(
            model=model,
            tools=list(tools),
            hooks=[hooks],
            callback_handler=None,
            retry_strategy=None,
        ),
        shared,
    )


@pytest.mark.parametrize(
    "role,output_type,valid",
    [
        ("analyst", ResearchResult, {"job_id": "a", "outcome": "success"}),
        ("wheel_expert", ExpertResult, {"assessment": "reviewed"}),
        ("team_lead", TeamResult, {"summary": "reviewed"}),
    ],
)
async def test_one_output_repair_is_allowed_then_third_generation_is_blocked(
    tmp_path, role, output_type, valid
):
    model = ScriptModel(
        output(output_type.__name__),
        output(output_type.__name__),
        output(output_type.__name__, **valid),
    )
    agent, shared = hooked_agent(tmp_path, model, role, max_output_repairs=1)
    with pytest.raises(Exception) as caught:
        await agent.invoke_async("Review", structured_output_model=output_type)
    assert models.invocation_failure(caught.value) == "output_repairs"
    assert models.budget_failure(caught.value) is None
    assert shared.generations == 2
    assert len(model.calls) == 2


async def test_ordinary_tool_error_does_not_start_output_repair_limit(tmp_path):
    @tool
    async def knowledge_read(module_id: str) -> dict:
        """Read a deliberately unavailable development module."""
        raise ValueError("Unavailable")

    model = ScriptModel(
        output("knowledge_read", module_id="missing"),
        output("ResearchResult", job_id="a", outcome="partial", summary="Source unavailable"),
    )
    agent, shared = hooked_agent(tmp_path, model, tools=[knowledge_read], max_output_repairs=0)
    result = await agent.invoke_async("Review", structured_output_model=ResearchResult)
    assert result.structured_output.summary == "Source unavailable"
    assert shared.generations == 2


async def test_repair_allowance_counts_model_generations_even_when_repair_uses_a_tool(tmp_path):
    @tool
    async def knowledge_read(module_id: str) -> dict:
        """Return an explicitly labelled development source."""
        return {"module_id": module_id}

    model = ScriptModel(
        output("ResearchResult"),
        output("knowledge_read", module_id="development"),
        output("ResearchResult", job_id="a", outcome="success"),
    )
    agent, shared = hooked_agent(tmp_path, model, tools=[knowledge_read], max_output_repairs=1)
    with pytest.raises(Exception) as caught:
        await agent.invoke_async("Review", structured_output_model=ResearchResult)
    assert models.invocation_failure(caught.value) == "output_repairs"
    assert shared.generations == 2


async def test_default_repair_policy_preserves_sdk_repair_behavior(tmp_path):
    model = ScriptModel(
        output("ResearchResult"),
        output("ResearchResult"),
        output("ResearchResult", job_id="a", outcome="success", summary="Validated"),
    )
    agent, shared = hooked_agent(tmp_path, model)
    result = await agent.invoke_async("Review", structured_output_model=ResearchResult)
    assert result.structured_output.summary == "Validated"
    assert shared.generations == 3


async def test_invocation_ceiling_leaves_shared_generations_available_for_lead(tmp_path):
    @tool
    async def knowledge_read(module_id: str) -> dict:
        """Read a development source."""
        return {"module_id": module_id}

    shared = budget()
    shared.max_generations = 4
    analyst = ScriptModel(
        output("knowledge_read", module_id="a"),
        output("knowledge_read", module_id="b"),
        output("ResearchResult", job_id="a", outcome="success"),
    )
    child, _ = hooked_agent(
        tmp_path, analyst, shared=shared, tools=[knowledge_read], generation_ceiling=2
    )
    with pytest.raises(Exception) as caught:
        await child.invoke_async("Review", structured_output_model=ResearchResult)
    assert models.invocation_failure(caught.value) == "generation_reserve"
    assert models.budget_failure(caught.value) is None
    lead_model = ScriptModel(output("TeamResult", summary="Preserved lead generation"))
    lead, _ = hooked_agent(tmp_path, lead_model, "team_lead", shared=shared)
    result = await lead.invoke_async("Synthesize", structured_output_model=TeamResult)
    assert result.structured_output.summary == "Preserved lead generation"
    assert shared.generations == 3


async def test_global_generation_limit_takes_precedence_over_invocation_limit(tmp_path):
    shared = budget()
    shared.max_generations = shared.generations = 2
    model = ScriptModel(output("TeamResult", summary="Must not run"))
    agent, _ = hooked_agent(tmp_path, model, "team_lead", shared=shared, generation_ceiling=2)
    with pytest.raises(Exception) as caught:
        await agent.invoke_async("Review", structured_output_model=TeamResult)
    assert models.budget_failure(caught.value) == "generation"
    assert models.invocation_failure(caught.value) is None
    assert not model.calls


async def test_accepted_generation_refreshes_context_and_traces_public_stop_reason(tmp_path):
    observed = []

    @tool
    async def knowledge_read(module_id: str) -> dict:
        """Read a development source."""
        return {"module_id": module_id}

    shared = budget()

    def refresh(agent):
        observed.append(shared.generations)
        agent.system_prompt = f"generation {shared.generations}"

    model = ScriptModel(
        output("knowledge_read", module_id="a"),
        output("ResearchResult", job_id="a", outcome="success"),
    )
    agent, _ = hooked_agent(
        tmp_path, model, shared=shared, tools=[knowledge_read], refresh_context=refresh
    )
    await agent.invoke_async("Review", structured_output_model=ResearchResult)
    assert observed == [1, 2]
    assert agent.system_prompt == "generation 2"
    rows = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    finished = [row for row in rows if row["event"] == "model_generation_finished"]
    assert [row["stop_reason"] for row in finished] == ["tool_use", "tool_use"]
    assert "reasoning" not in json.dumps(rows)


async def test_role_context_refresh_preserves_prefix_and_updates_actual_remaining_budget(
    tmp_path, monkeypatch
):
    from tests.thin.test_agents import context
    from wheel_team.agents import base
    from wheel_team.config import FinalizationConfig

    clock = [100.0]
    monkeypatch.setattr(base, "time", SimpleNamespace(monotonic=lambda: clock[0]), raising=False)
    seen = []

    class InspectModel(ScriptModel):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            seen.append(system_prompt)
            async for chunk in super().stream(messages, tool_specs, system_prompt, **kwargs):
                yield chunk

    async def knowledge_read(module_id: str) -> dict:
        """Return a development source and advance the controlled monotonic clock."""
        clock[0] = 104.0
        ctx.budget.expert_consultations = 1
        return {"module_id": module_id}

    model = InspectModel(
        output("knowledge_read", module_id="a"),
        output("ResearchResult", job_id="a", outcome="success"),
    )
    ctx = context(tmp_path, "analyst", model, {"knowledge_read": knowledge_read}, limit=10)
    ctx.monotonic_deadline = 150.0
    ctx.invocation_deadline = 130.0
    ctx.generation_ceiling = 7
    ctx.config = ctx.config.model_copy(
        update={"finalization": FinalizationConfig(max_output_repairs=1)}
    )
    ctx.prompt = ctx.prompt.model_copy(
        update={
            "status": "approved",
            "owner_approval_ref": "owner:approved-test-assets",
            "source_path": "/private/owner/secret-source.md",
        }
    )
    prefix = ctx.prompt.content
    await base.build_role("analyst", ctx).invoke_async(
        "Review", structured_output_model=ResearchResult
    )
    assert len(seen) == 2
    assert all(p.startswith(prefix + "\n\nRun context (data):\n") for p in seen)
    assert all(p.count("Run context (data):\n") == 1 for p in seen)
    first, second = [json.loads(p.split("Run context (data):\n")[1]) for p in seen]
    assert first["runtime_budget"] == {
        "global_seconds_remaining": 50.0,
        "invocation_seconds_remaining": 30.0,
        "shared_generations_remaining": 9,
        "invocation_generations_remaining": 6,
        "expert_consultations_remaining": 5,
    }
    assert second["runtime_budget"] == {
        "global_seconds_remaining": 46.0,
        "invocation_seconds_remaining": 26.0,
        "shared_generations_remaining": 8,
        "invocation_generations_remaining": 5,
        "expert_consultations_remaining": 4,
    }
    assert first["analysis_time"] == second["analysis_time"] == "2026-10-31T12:00:00+00:00"
    assert first["role"] == "analyst"
    assert first["prompt_identity"]["content_hash"] == ctx.prompt.content_hash
    assert first["prompt_identity"]["status"] == "approved"
    assert first["prompt_identity"]["owner_approval_ref"] == "owner:approved-test-assets"
    assert "/private/owner" not in json.dumps(first)
    assert first["output_contract"]["tool_name"] == "ResearchResult"
    assert first["output_contract"]["required_fields"] == ["job_id", "outcome"]
    assert first["output_contract"]["required_field_types"] == {
        "job_id": "string",
        "outcome": "string",
    }
    assert first["output_contract"]["valid_outcomes"] == [
        "success",
        "partial",
        "failed",
        "cancelled",
        "budget_exhausted",
    ]
    assert first["output_contract"]["max_output_tokens"] == 4096
    assert first["output_contract"]["max_output_repairs_after_failed_output_tool"] == 1
    assert ctx.prompt.content == prefix
    assert {spec["name"] for spec in model.calls[0]["tools"]} == {
        "knowledge_read",
        "ResearchResult",
    }
    assert all(call["tool_choice"] is None for call in model.calls)


@pytest.mark.parametrize(
    "role,output_type,valid,required",
    [
        ("wheel_expert", ExpertResult, {"assessment": "reviewed"}, ["assessment"]),
        ("team_lead", TeamResult, {"summary": "reviewed"}, ["summary"]),
    ],
)
async def test_role_output_contract_names_required_fields_and_host_owned_metadata(
    tmp_path, role, output_type, valid, required
):
    from tests.thin.test_agents import context
    from wheel_team.agents.base import build_role

    model = ScriptModel(output(output_type.__name__, **valid))
    agent = build_role(role, context(tmp_path, role, model))
    await agent.invoke_async("Review", structured_output_model=output_type)
    data = json.loads(agent.system_prompt.split("Run context (data):\n")[1])
    contract = data["output_contract"]
    assert contract["tool_name"] == output_type.__name__
    assert contract["required_fields"] == required
    assert contract["max_output_repairs_after_failed_output_tool"] is None
    assert data["runtime_budget"]["global_seconds_remaining"] is None
    assert data["runtime_budget"]["invocation_seconds_remaining"] is None
    assert data["runtime_budget"]["invocation_generations_remaining"] is None
    if role == "team_lead":
        assert {
            "supporting_records",
            "artifacts",
            "prompt_identities",
            "model_identity",
            "report_markdown",
        } <= set(contract["omit_host_populated_fields"])
        assert "summary" not in contract["omit_host_populated_fields"]


async def test_role_builder_applies_configured_zero_output_repair_allowance(tmp_path):
    from tests.thin.test_agents import context
    from wheel_team.agents.base import build_role
    from wheel_team.config import FinalizationConfig

    model = ScriptModel(
        output("ResearchResult"),
        output("ResearchResult", job_id="a", outcome="success"),
    )
    ctx = context(tmp_path, "analyst", model)
    ctx.config = ctx.config.model_copy(
        update={"finalization": FinalizationConfig(max_output_repairs=0)}
    )
    with pytest.raises(Exception) as caught:
        await build_role("analyst", ctx).invoke_async(
            "Review", structured_output_model=ResearchResult
        )
    assert models.invocation_failure(caught.value) == "output_repairs"
    assert ctx.budget.generations == 1
