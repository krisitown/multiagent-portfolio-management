"""Independent public researcher; no portfolio, orchestrator or continuity authority."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator
from strands import Agent, tool
from strands.agent.conversation_manager import NullConversationManager
from strands.hooks.events import AfterModelCallEvent, AfterToolCallEvent
from strands.models import Model

from portfolio_management.adapters.market.spark import SSHResearchConfig
from portfolio_management.adapters.research import ResearchBackendConfig
from portfolio_management.config import ModelConfig
from portfolio_management.contracts import JsonValue
from portfolio_management.model_observer import CompletionTelemetry
from portfolio_management.models import RunHooks, InvocationLimitExceeded
from portfolio_management.tools.evidence import evidence_tools
from portfolio_management.tools.finance import finance_tools
from portfolio_management.tools.market import market_tools
from portfolio_management.tools.option_scenarios import option_scenario_tools
from portfolio_management.tools.research import research_tools
from portfolio_management.types import RunBudget, TraceSink
from portfolio_management.vault import EvidenceStore

PUBLIC_TOOLS = frozenset(
    {
        "public_search",
        "fetch_sources",
        "market_query",
        "evidence_read",
        "evidence_find",
        "finance_calculate",
        "option_scenarios",
    }
)


class AsymmetricConfig(BaseModel):
    """Only runtime/public providers; no account, history, feedback or memory input."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    model: ModelConfig
    max_generations: int = Field(default=24, gt=0, le=1000, strict=True)
    deadline_seconds: float = Field(default=900, gt=0, le=14400, allow_inf_nan=False)
    research_backend: ResearchBackendConfig | None = None
    market_backend: SSHResearchConfig | None = None
    market_capture_manifest_path: Path | None = None

    @model_validator(mode="after")
    def selected_market(self) -> "AsymmetricConfig":
        if self.market_backend is not None and self.market_capture_manifest_path is not None:
            raise ValueError("Select connected market or capture replay, not both")
        return self


class AsymmetricHooks(RunHooks):
    """Reuse accepted-generation accounting; persist ordinary outputs, never reasoning."""

    def __init__(
        self, budget: RunBudget, trace: TraceSink, telemetry: CompletionTelemetry | None = None
    ):
        # Continuity has ordinary final text and no forced output tool. No continuity
        # role or tools are constructed; only the shared hook accounting is reused.
        super().__init__(budget, trace, "continuity", "standalone")
        self.role = "asymmetric_opportunities"
        self.telemetry = telemetry
        self.ordinary_messages: list[dict[str, Any]] = []
        self.last_text = ""
        self.local_generations = 0
        self.local_generation_limit: int | None = None
        self.usages: list[dict[str, Any] | None] = []

    def before_model(self, event: Any) -> None:
        if self.local_generation_limit is not None and self.local_generations >= self.local_generation_limit:
            raise InvocationLimitExceeded("generation_reserve")
        if self.telemetry is not None:
            self.telemetry.usage = None
            self.telemetry.finish_reason = None
        super().before_model(event)
        self.local_generations += 1

    def after_model(self, event: AfterModelCallEvent) -> None:
        active = self.active
        super().after_model(event)
        if active is None:
            return
        message: dict[str, Any] = (
            cast(dict[str, Any], event.stop_response.message)
            if event.stop_response is not None
            else {}
        )
        # Explicit whitelist: no raw messages, reasoning blocks or thinking fields.
        text = "".join(
            block["text"]
            for block in message.get("content", [])
            if isinstance(block.get("text"), str)
        )
        if text:
            self.last_text = text
            ordinary: dict[str, JsonValue] = {
                "generation": active[0],
                "text": text,
                "stop_reason": event.stop_response.stop_reason if event.stop_response else None,
            }
            self.ordinary_messages.append(ordinary)
            self.trace.emit("assistant_text", role=self.role, invocation_id=self.invocation_id, **ordinary)
        usage: dict[str, Any] | None = message.get("metadata", {}).get("usage")
        if self.telemetry is not None:
            snapshot = self.telemetry.snapshot()
            self.trace.emit("completion_metadata", **snapshot)
            provider_usage = snapshot.get("usage")
            if provider_usage:
                usage = {
                    target: provider_usage[source]
                    for source, target in (
                        ("prompt_tokens", "inputTokens"),
                        ("completion_tokens", "outputTokens"),
                        ("total_tokens", "totalTokens"),
                    )
                    if source in provider_usage
                }
        self.usages.append(usage)

    def after_tool(self, event: AfterToolCallEvent) -> None:
        # Preserve normal error tool responses as evidence, withholding exception text
        # which can contain transport headers or credentials.
        self.trace.emit(
            "tool_call_finished",
            role=self.role,
            invocation_id=self.invocation_id,
            name=event.tool_use["name"],
            call_id=event.tool_use["toolUseId"],
            status=event.result.get("status", "error"),
            elapsed_seconds=event.duration,
            result=cast(JsonValue, event.result.get("content"))
            if event.exception is None
            else {"validation_error": "Tool invocation rejected; supply the advertised object schema"},
            error_category=type(event.exception).__name__ if event.exception else None,
        )


def build_asymmetric(
    *,
    model: Model,
    prompt: str,
    store: EvidenceStore,
    budget: RunBudget,
    trace: TraceSink,
    market_provider: Any = None,
    research_backend: Any = None,
    telemetry: CompletionTelemetry | None = None,
    invocation_limits: dict[str, Any] | None = None,
    fact_check: Any = None,
    fact_check_max_calls: int = 3,
) -> tuple[Agent, AsymmetricHooks]:
    """Construct one actual Strands agent with only new-run public tool bindings."""
    if not prompt.strip():
        raise ValueError("An exact nonempty specialist prompt is required")
    available = {
        **evidence_tools(store),
        **finance_tools(store, clock=trace.clock),
        **market_tools(store, market_provider),
        **research_tools(store, research_backend, trace.clock),
        **option_scenario_tools(store, trace.clock),
    }
    bindings: list[Any] = [
        tool(name=name, description=available[name].__doc__ or name)(available[name])
        for name in sorted(PUBLIC_TOOLS)
    ]
    if fact_check is not None:
        bindings.append(tool(name="fact_check", description=fact_check.__doc__ or "Ask a fresh public reasoning checker")(fact_check))
    # Advertise the full nested contract using exact decimal strings. SDK coercion
    # accepts a JSON string for recovery; the advertised input remains an object.
    from portfolio_management.domain.option_pricing import ScenarioRequest
    schema = ScenarioRequest.model_json_schema()
    def exact_schema(value):
        if isinstance(value, dict):
            if "anyOf" in value and any(x.get("type") == "number" for x in value["anyOf"]):
                value["anyOf"] = [x for x in value["anyOf"] if x.get("type") != "number"]
            for child in value.values(): exact_schema(child)
        elif isinstance(value, list):
            for child in value: exact_schema(child)
    exact_schema(schema)
    for binding in bindings:
        if binding.tool_name == "option_scenarios":
            binding.tool_spec["inputSchema"]["json"] = {"type": "object", "$defs": schema.pop("$defs", {}), "properties": {"request": schema}, "required": ["request"], "additionalProperties": False}
    hooks = AsymmetricHooks(budget, trace, telemetry)
    run_context = {
        "analysis_time": trace.clock().isoformat(),
        "market_provider": market_provider.describe()
        if hasattr(market_provider, "describe")
        else {"mode": "unavailable" if market_provider is None else "injected"},
        "research_backend": research_backend.describe()
        if research_backend is not None
        else {"mode": "unavailable"},
        "max_generations": budget.max_generations,
        "deadline": budget.deadline.isoformat(),
        "output": "ordinary final assistant text; no forced template",
        "fact_check": {"available": fact_check is not None, "scope": "author-directed public reasoning feedback; no private originals", "max_calls": fact_check_max_calls, "requirement": "Receive completed feedback before final; repair claims and disclose unresolved limitations. Completion is not approval."} if fact_check is not None else {"available": False},
        **(invocation_limits or {}),
    }
    agent = Agent(
        model=model,
        system_prompt=prompt + "\n\nRuntime context (data):\n" + json.dumps(run_context),
        tools=bindings,
        hooks=[hooks],
        callback_handler=None,
        retry_strategy=None,
        conversation_manager=NullConversationManager(),
    )
    return agent, hooks


def utc_now() -> datetime:
    return datetime.now(UTC)
