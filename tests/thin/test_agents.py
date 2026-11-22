"""The role boundary cannot leak market, mutation or delegation tools."""

import asyncio
import json
from datetime import timedelta

import pytest

from tests.support.thin_fakes import fixed_clock
from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_entrypoint import config
from portfolio_management.agents.analyst import build_analyst
from portfolio_management.agents.team_lead import build_team_lead
from portfolio_management.agents.wheel_expert import build_wheel_expert
from portfolio_management.contracts import AgentRole
from portfolio_management.prompt_loader import load_prompt
from portfolio_management.types import RoleContext, RunBudget, TeamResult, TraceSink


def context(tmp_path, role, model, tools=None, limit=100):
    cfg = config(tmp_path)
    return RoleContext(
        request={"question": "Explain assignment", "mode": "education"},
        config=cfg,
        model=model,
        prompt=load_prompt(AgentRole(role), cfg.prompts),
        tools=tools or {},
        budget=RunBudget(limit, 5, fixed_clock() + timedelta(minutes=45), asyncio.Semaphore(4)),
        trace=TraceSink(tmp_path / "trace.jsonl", fixed_clock),
        run_dir=tmp_path,
    )


@pytest.mark.parametrize(
    "role,factory,allowed,blocked",
    [
        ("team_lead", build_team_lead, "research_batch", "market_query"),
        ("analyst", build_analyst, "market_query", "consult_expert"),
        ("wheel_expert", build_wheel_expert, "finance_calculate", "market_query"),
        ("analyst", build_analyst, "public_search", "consult_expert"),
        ("analyst", build_analyst, "fetch_sources", "research_batch"),
        ("team_lead", build_team_lead, "research_batch", "public_search"),
        ("wheel_expert", build_wheel_expert, "finance_calculate", "fetch_sources"),
    ],
)
async def test_only_role_permitted_tools_are_supplied(tmp_path, role, factory, allowed, blocked):
    async def visible(value: str = "") -> dict:
        """Record an observable value."""
        return {"value": value}

    model = ScriptModel(output("TeamResult", summary="safe"))
    agent = factory(context(tmp_path, role, model, {allowed: visible, blocked: visible}))
    await agent.invoke_async("question", structured_output_model=TeamResult)
    names = {spec["name"] for spec in model.calls[0]["tools"]}
    assert allowed in names
    assert blocked not in names


async def test_factories_create_independent_conversations_and_safe_trace(tmp_path):
    first = ScriptModel(output("TeamResult", summary="one"))
    second = ScriptModel(output("TeamResult", summary="two"))
    a = build_analyst(context(tmp_path, "analyst", first))
    b = build_analyst(context(tmp_path, "analyst", second))
    await a.invoke_async("private question A", structured_output_model=TeamResult)
    await b.invoke_async("question B", structured_output_model=TeamResult)
    assert a.messages is not b.messages
    assert "private question A" not in json.dumps(second.calls)
    rows = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert sum(row["event"] == "model_generation_started" for row in rows) == 2
    assert "messages" not in json.dumps(rows)


async def test_prompt_role_mismatch_rejected(tmp_path):
    ctx = context(tmp_path, "analyst", ScriptModel())
    with pytest.raises(ValueError, match="prompt role"):
        build_wheel_expert(ctx)


async def test_supplied_snapshot_reference_is_visible_to_the_sdk_lead(tmp_path):
    from tests.thin.test_entrypoint import snapshot
    from portfolio_management.team import run_team
    from portfolio_management.types import TeamDependencies, TeamRequest

    class SnapshotModel(ScriptModel):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            assert "synthetic-snapshot" not in system_prompt
            assert "synthetic-snapshot" in json.dumps(messages)
            assert '"mode": "portfolio"' in system_prompt
            async for chunk in super().stream(messages, tool_specs, system_prompt, **kwargs):
                yield chunk

    model = SnapshotModel(output("TeamResult", summary="snapshot reference received"))
    result = await run_team(
        TeamRequest(question="Assess", mode="portfolio", portfolio=snapshot()),
        config=config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: model),
    )
    assert result.summary == "snapshot reference received"


async def test_decorated_override_cannot_smuggle_another_role_tool(tmp_path):
    from strands import tool

    @tool(name="market_query")
    async def market_query(query: dict) -> dict:
        """Market query forbidden to expert."""
        return query

    with pytest.raises(ValueError, match="tool name"):
        build_wheel_expert(
            context(
                tmp_path,
                "wheel_expert",
                ScriptModel(),
                {
                    "knowledge_read": market_query,
                },
            )
        )


@pytest.mark.parametrize("status", [200, 429, 500, 307])
async def test_configured_openai_sdk_transport_has_no_retries_redirects_or_proxy(
    tmp_path, monkeypatch, status
):
    import httpx

    from portfolio_management import models
    from portfolio_management.config import ModelConfig
    from portfolio_management.team import run_team
    from portfolio_management.types import TeamDependencies, TeamRequest

    requests = []
    clients = []
    original = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        if status != 200:
            return httpx.Response(
                status,
                json={"error": {"message": "private provider text"}},
                headers={"Location": "https://wrong-endpoint.invalid"},
            )
        payload = json.loads(request.content)
        assert payload["model"] == "explicit-offline-model"
        assert payload["max_tokens"] == 123
        stream = [
            {
                "id": "offline",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "explicit-offline-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": None,
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call1",
                                    "type": "function",
                                    "function": {
                                        "name": "TeamResult",
                                        "arguments": '{"summary":"transport passed"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
            {
                "id": "offline",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "explicit-offline-model",
                "choices": [{"index": 0, "finish_reason": "tool_calls", "delta": {}}],
            },
        ]
        body = "".join("data: " + json.dumps(item) + "\n\n" for item in stream) + "data: [DONE]\n\n"
        return httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})

    class ControlledClient(original):
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False
            super().__init__(**kwargs, transport=httpx.MockTransport(handle))
            clients.append(self)

    monkeypatch.setattr(models.httpx, "AsyncClient", ControlledClient)
    monkeypatch.setenv("THIN_OFFLINE_TEST_KEY", "sk-private-test-value")
    monkeypatch.setenv("HTTPS_PROXY", "http://must-not-use.invalid")
    cfg = config(tmp_path).model_copy(
        update={
            "model": ModelConfig(
                provider="openai",
                model_id="explicit-offline-model",
                endpoint="https://configured.invalid/v1",
                api_key_env="THIN_OFFLINE_TEST_KEY",
                allow_inference_network=True,
                max_output_tokens=123,
            )
        }
    )
    result = await run_team(
        TeamRequest(question="Explain", mode="education"),
        config=cfg,
        deps=TeamDependencies(evaluation_mode="team"),
    )
    assert len(requests) == 1
    assert str(requests[0].url) == "https://configured.invalid/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer sk-private-test-value"
    assert all(client.is_closed for client in clients)
    assert result.completion_reason == ("complete" if status == 200 else "failed")
    if status == 200:
        assert result.summary == "transport passed"
    for path in result.artifacts.values():
        content = __import__("pathlib").Path(path).read_text()
        assert "sk-private-test-value" not in content
        assert "private provider text" not in content


async def test_unapproved_network_selection_fails_before_client_construction(tmp_path, monkeypatch):
    from portfolio_management import models
    from portfolio_management.config import ModelConfig
    from portfolio_management.team import run_team
    from portfolio_management.types import TeamDependencies, TeamRequest

    def forbidden(**kwargs):
        raise AssertionError("inference client must not be constructed")

    monkeypatch.setattr(models, "AsyncOpenAI", forbidden)
    cfg = config(tmp_path).model_copy(
        update={
            "model": ModelConfig(
                provider="openai",
                model_id="offline",
                endpoint="https://configured.invalid/v1",
                api_key_env="MISSING_KEY",
                allow_inference_network=False,
            )
        }
    )
    with pytest.raises(ValueError, match="allow_inference_network"):
        await run_team(
            TeamRequest(question="Explain", mode="education"),
            config=cfg,
            deps=TeamDependencies(evaluation_mode="team"),
        )
    assert not list(tmp_path.glob("run-*"))


async def test_lead_receives_only_coordination_tools(tmp_path):
    async def read(value: str = "") -> dict:
        """Read a synthetic value."""
        return {"value": value}

    model = ScriptModel(output("TeamResult", summary="coordinated"))
    tools = {
        name: read
        for name in (
            "research_batch",
            "consult_expert",
            "evidence_read",
            "evidence_find",
            "finance_calculate",
            "knowledge_read",
            "portfolio_read",
            "market_query",
            "publish_review",
        )
    }
    agent = build_team_lead(context(tmp_path, "team_lead", model, tools))
    await agent.invoke_async("Coordinate", structured_output_model=TeamResult)
    names = {spec["name"] for spec in model.calls[0]["tools"]}
    assert names == {
        "research_batch",
        "consult_expert",
        "TeamResult",
    }


@pytest.mark.parametrize("role", ["team_lead", "analyst", "wheel_expert"])
async def test_role_runtime_data_discloses_selected_provider_capabilities_and_scope(tmp_path, role):
    class ReplayMetadata:
        def capabilities(self):
            return ["fundamentals", "underlying_quotes"]

        def describe(self):
            return {
                "mode": "captured_replay",
                "provider": "integrity-test-provider",
                "coverage": {"market_wide": False, "scope": "test metadata only"},
                "sources": ["labelled-test-source"],
            }

    class ContextModel(ScriptModel):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            self.runtime_data = json.loads(system_prompt.split("Run context (data):\n")[1])
            async for chunk in super().stream(messages, tool_specs, system_prompt, **kwargs):
                yield chunk

    class WebMetadata:
        def describe(self):
            return {
                "mode": "connected",
                "provider": "labelled-test-web-provider",
                "capabilities": ["public_search", "fetch_sources"],
            }

    model = ContextModel(output("TeamResult", summary="observed metadata"))
    ctx = context(tmp_path, role, model)
    ctx.market_provider = ReplayMetadata()
    ctx.research_backend = WebMetadata()
    factory = {
        "team_lead": build_team_lead,
        "analyst": build_analyst,
        "wheel_expert": build_wheel_expert,
    }[role]
    await factory(ctx).invoke_async("Inspect capabilities", structured_output_model=TeamResult)
    assert model.runtime_data["analysis_time"] == "2026-10-31T12:00:00+00:00"
    assert model.runtime_data["run_id"] == tmp_path.name
    market = model.runtime_data["market_provider"]
    assert market["capabilities"] == ["fundamentals", "underlying_quotes"]
    assert market["mode"] == "captured_replay"
    assert market["coverage"]["market_wide"] is False
    assert market["sources"] == ["labelled-test-source"]
    web = model.runtime_data["research_backend"]
    assert web["mode"] == "connected"
    assert web["capabilities"] == ["public_search", "fetch_sources"]


async def test_market_tool_sdk_schema_supplies_required_fields_and_rehydrates_query(tmp_path):
    from portfolio_management.tools.market import market_tools
    from portfolio_management.vault import EvidenceStore

    model = ScriptModel(
        output("market_query", query={"query_id": "schema-check", "capability": "fundamentals"}),
        output("TeamResult", summary="Unavailable dependency inspected"),
    )
    tools = market_tools(EvidenceStore(tmp_path / "records"), None)
    agent = build_analyst(context(tmp_path, "analyst", model, tools))
    await agent.invoke_async("Inspect unavailable capability", structured_output_model=TeamResult)
    spec = next(spec for spec in model.calls[0]["tools"] if spec["name"] == "market_query")
    query_schema = spec["inputSchema"]["json"]["$defs"]["MarketQuery"]
    assert set(query_schema["required"]) == {"query_id", "capability"}
    assert "Market capability is unavailable" in json.dumps(model.calls[1]["messages"])


@pytest.mark.parametrize(
    "query,expected_status,expected_requests",
    [
        (
            '{"query_id":"pep-quotes-01","capability":"underlying_quotes","instrument_ids":["PEP"]}',
            "unavailable",
            [("pep-quotes-01", "underlying_quotes", ("PEP",))],
        ),
        ('{"query_id":', "invalid_data", []),
    ],
)
async def test_sdk_market_query_accepts_observed_json_text_and_handles_malformed_text(
    tmp_path, query, expected_status, expected_requests
):
    from portfolio_management.contracts import MarketResult
    from portfolio_management.tools.market import market_tools
    from portfolio_management.vault import EvidenceStore

    requests = []

    class NoObservationProvider:
        def capabilities(self):
            return ["underlying_quotes"]

        async def query(self, request):
            requests.append((request.query_id, request.capability, request.instrument_ids))
            return MarketResult(
                query_id=request.query_id,
                status="unavailable",
                reason="Labelled SDK compatibility test; no market observations",
            )

    model = ScriptModel(
        output("market_query", query=query),
        output("TeamResult", summary="Compatibility result inspected"),
    )
    tools = market_tools(EvidenceStore(tmp_path / "records"), NoObservationProvider())
    agent = build_analyst(context(tmp_path, "analyst", model, tools))
    await agent.invoke_async("Inspect protocol compatibility", structured_output_model=TeamResult)
    tool_result = model.calls[1]["messages"][-1]["content"][0]["toolResult"]
    assert tool_result["status"] == "success"
    payload = json.loads(tool_result["content"][0]["text"])
    assert payload["status"] == expected_status
    assert requests == expected_requests
    schema = next(spec for spec in model.calls[0]["tools"] if spec["name"] == "market_query")[
        "inputSchema"
    ]["json"]
    assert {"type": "string"} in schema["properties"]["query"]["anyOf"]
    assert set(schema["$defs"]["MarketQuery"]["required"]) == {"query_id", "capability"}


async def test_sdk_finance_schema_lists_exact_formulas_and_executes_mechanical_put(tmp_path):
    """Isolated option arithmetic; these labelled terms are not issuer observations."""
    from portfolio_management.tools.finance import finance_tools
    from portfolio_management.vault import EvidenceStore

    contract = {
        "instrument_id": "isolated-mechanical-put",
        "instrument_type": "option",
        "currency": "USD",
        "underlying_id": "isolated-mechanical-underlying",
        "expiry": "2026-12-18",
        "option_right": "put",
        "strike": "50",
        "multiplier": "100",
    }
    model = ScriptModel(
        output(
            "finance_calculate",
            formula="put_economics",
            inputs={"quantity": "2", "premium": "2", "fees": "3"},
            parameters={"contract": contract, "price_basis": "isolated_mechanical_example"},
        ),
        output("TeamResult", summary="Isolated mechanical calculation"),
    )
    tools = finance_tools(EvidenceStore(tmp_path / "records"), clock=fixed_clock)
    agent = build_analyst(context(tmp_path, "analyst", model, tools))
    await agent.invoke_async("Check isolated arithmetic", structured_output_model=TeamResult)
    spec = next(spec for spec in model.calls[0]["tools"] if spec["name"] == "finance_calculate")
    assert set(spec["inputSchema"]["json"]["properties"]["formula"]["enum"]) == {
        "put_economics",
        "roll_economics",
        "expiry_payoff",
        "match_coverage",
        "analyze_portfolio",
        "performance",
        "arithmetic",
    }
    result = model.calls[1]["messages"][-1]["content"][0]["toolResult"]
    assert result["status"] == "success"
    payload = json.loads(result["content"][0]["text"])
    assert payload["status"] == "success"
    assert payload["data"]["outputs"]["gross_assignment_funding"] == "10000"
    assert payload["data"]["outputs"]["net_premium"] == "397"
    assert payload["data"]["outputs"]["breakeven"] == "48.015"
