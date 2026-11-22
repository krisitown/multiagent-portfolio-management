"""Direct pinned Strands models and a run-local accepted-generation hook."""

import hashlib
import json
import os
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Any, cast
from urllib.parse import urlparse

import httpx
from openai import AsyncOpenAI
from strands.hooks import HookProvider, HookRegistry
from strands.hooks.events import (
    AfterModelCallEvent,
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
)
from strands.models import Model

from portfolio_management.config import ModelConfig
from portfolio_management.contracts import JsonValue
from portfolio_management.model_observer import (
    CompletionTelemetry,
    InferenceOpenAIModel,
    ObservedOpenAIModel,
)
from portfolio_management.types import RoleContext, RunBudget, TraceSink

TEAM_RESULT_CANDIDATE_SLOTS_GUIDANCE = (
    "candidate_slots: supply a JSON array of objects directly, never an encoded string or null. "
    "When empty, omit candidate_slots or use []. Each object must still pass native validation."
)


class GenerationLimitExceeded(RuntimeError):
    """No further model attempts may be accepted by this run."""


class RunDeadlineExceeded(RuntimeError):
    """The run's absolute deadline has passed."""


class InvocationLimitExceeded(RuntimeError):
    """This invocation must yield while the shared run can still continue."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def check_budget(budget: RunBudget, trace: TraceSink) -> None:
    if trace.clock() >= budget.deadline:
        raise RunDeadlineExceeded("Run deadline exceeded")
    if budget.generations >= budget.max_generations:
        raise GenerationLimitExceeded("Model generation limit exhausted")


def budget_failure(exc: BaseException) -> str | None:
    """The SDK wraps hook failures; follow the cause chain without exposing messages."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, GenerationLimitExceeded):
            return "generation"
        if isinstance(current, RunDeadlineExceeded):
            return "deadline"
        current = current.__cause__ or current.__context__
    return None


def invocation_failure(exc: BaseException) -> str | None:
    """Find a wrapped local limit without classifying it as shared exhaustion."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, InvocationLimitExceeded):
            return current.reason
        current = current.__cause__ or current.__context__
    return None


class RunHooks(HookProvider):
    """Count before every SDK attempt, including retries and output repairs."""

    def __init__(
        self,
        budget: RunBudget,
        trace: TraceSink,
        role: str,
        invocation_id: str,
        *,
        generation_ceiling: int | None = None,
        max_output_repairs: int | None = None,
        refresh_context: Callable[[Any], None] | None = None,
        context: RoleContext | None = None,
    ):
        self.budget, self.trace = budget, trace
        self.role, self.invocation_id = role, invocation_id
        self.generation_ceiling = generation_ceiling
        self.max_output_repairs = max_output_repairs
        self.refresh_context = refresh_context
        self.context = context
        self.repairs_used: int | None = None
        self.output_tool = {
            "explorer": "ResearchResult",
            "analyst": "ResearchResult",
            "wheel_expert": "ExpertResult",
            "dalio": "ExpertResult",
            "buffett": "ExpertResult",
            "portfolio_manager": "ExpertResult",
            "team_lead": "TeamResult",
            "fact_checker": None,
            "continuity": None,
        }[role]
        self.active: tuple[int, float] | None = None

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeModelCallEvent, self.before_model)
        registry.add_callback(AfterModelCallEvent, self.after_model)
        registry.add_callback(BeforeToolCallEvent, self.before_tool)
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def before_model(self, event: BeforeModelCallEvent) -> None:
        # No await between the shared check/increment. No permit is held over tools.
        check_budget(self.budget, self.trace)
        if (
            self.context
            and self.context.terminal_only
            and self.context.invocation_deadline is not None
            and time.monotonic() >= self.context.invocation_deadline
        ):
            raise InvocationLimitExceeded("time_reserve")
        ceiling = self.context.generation_ceiling if self.context else self.generation_ceiling
        if ceiling is not None and self.budget.generations >= ceiling:
            raise InvocationLimitExceeded("generation_reserve")
        if (
            self.repairs_used is not None
            and self.max_output_repairs is not None
            and self.repairs_used >= self.max_output_repairs
        ):
            raise InvocationLimitExceeded("output_repairs")
        self.budget.generations += 1
        if self.repairs_used is not None:
            self.repairs_used += 1
            if self.context and self.max_output_repairs is not None:
                self.context.output_repairs_exhausted = self.repairs_used >= self.max_output_repairs
        self.active = self.budget.generations, time.perf_counter()
        if self.refresh_context is not None:
            self.refresh_context(event.agent)
        self.trace.emit(
            "model_generation_started",
            role=self.role,
            invocation_id=self.invocation_id,
            generation=self.budget.generations,
        )

    def after_model(self, event: AfterModelCallEvent) -> None:
        active, self.active = self.active, None
        if active is None:  # A rejected before-hook also receives an after-event.
            return
        generation, started = active
        self._save_public_lead_draft(event, generation)
        usage = None
        if event.stop_response is not None:
            source = cast(
                dict[str, Any], event.stop_response.message.get("metadata", {}).get("usage", {})
            )
            usage = {
                key: source[key]
                for key in ("inputTokens", "outputTokens", "totalTokens")
                if isinstance(source.get(key), (int, float))
            }
        self.trace.emit(
            "model_generation_finished",
            role=self.role,
            invocation_id=self.invocation_id,
            generation=generation,
            elapsed_seconds=time.perf_counter() - started,
            status="failed" if event.exception else "success",
            usage=usage,
            stop_reason=event.stop_response.stop_reason if event.stop_response else None,
            error_category=type(event.exception).__name__ if event.exception else None,
        )

    def _save_public_lead_draft(self, event: AfterModelCallEvent, generation: int) -> None:
        if (
            self.role != "team_lead"
            or self.context is None
            or self.context.config.child_output_mode == "direct_text"
            or event.exception is not None
            or event.stop_response is None
            or event.stop_response.stop_reason != "end_turn"
        ):
            return
        content = cast(list[dict[str, Any]], event.stop_response.message.get("content", []))
        if any("toolUse" in block for block in content):
            return
        text = "".join(block["text"] for block in content if isinstance(block.get("text"), str))
        if not text.strip():
            return
        encoded = text.encode("utf-8")
        text_sha256 = hashlib.sha256(encoded).hexdigest()
        metadata = {
            "validation_status": "unvalidated",
            "role": self.role,
            "invocation_id": self.invocation_id,
            "generation": generation,
            "captured_at": self.trace.clock().isoformat(),
            "stop_reason": event.stop_response.stop_reason,
            "text_chars": len(text),
            "text_sha256": text_sha256,
            "provenance": "Completed ordinary public assistant text; native finalization pending.",
        }
        for name, payload in (
            (f"unvalidated-lead-draft-{generation}.txt", encoded),
            (
                f"unvalidated-lead-draft-{generation}.json",
                (json.dumps(metadata, indent=2) + "\n").encode(),
            ),
        ):
            target = self.context.run_dir / name
            temporary = target.with_suffix(target.suffix + ".tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(payload)
            os.replace(temporary, target)
        self.trace.emit(
            "unvalidated_lead_draft_saved",
            role=self.role,
            invocation_id=self.invocation_id,
            generation=generation,
            text_chars=len(text),
            text_sha256=text_sha256,
        )

    def before_tool(self, event: BeforeToolCallEvent) -> None:
        if (
            self.context
            and self.context.terminal_only
            and event.tool_use["name"] != self.output_tool
        ):
            event.cancel_tool = "Research tools are disabled during analyst terminal finalization"
        self.trace.emit(
            "tool_call_started",
            role=self.role,
            invocation_id=self.invocation_id,
            name=event.tool_use["name"],
            call_id=event.tool_use["toolUseId"],
            inputs=event.tool_use.get("input", {}),
        )

    def after_tool(self, event: AfterToolCallEvent) -> None:
        failed = event.exception is not None or event.result.get("status") != "success"
        inputs = event.tool_use.get("input", {})
        if (
            self.role == "team_lead"
            and event.tool_use["name"] == "TeamResult"
            and failed
            and isinstance(inputs, dict)
            and "candidate_slots" in inputs
            and not isinstance(inputs["candidate_slots"], (list, tuple))
        ):
            event.result = {
                **event.result,
                "content": [
                    *event.result.get("content", []),
                    {"text": TEAM_RESULT_CANDIDATE_SLOTS_GUIDANCE},
                ],
            }
        if event.tool_use["name"] == self.output_tool and failed and self.repairs_used is None:
            self.repairs_used = 0
            if self.context and self.max_output_repairs == 0:
                self.context.output_repairs_exhausted = True
        # Exception messages can contain HTTP headers/body/credentials. Record only category.
        self.trace.emit(
            "tool_call_finished",
            role=self.role,
            invocation_id=self.invocation_id,
            name=event.tool_use["name"],
            call_id=event.tool_use["toolUseId"],
            status=event.result.get("status", "error"),
            elapsed_seconds=event.duration,
            result=cast(JsonValue, event.result.get("content"))
            if event.exception is None and event.result.get("status") == "success"
            else None,
            error_category=type(event.exception).__name__ if event.exception else None,
        )


class ScriptedModel(Model):
    """Explicit development-only result fixture; no inference client is created."""

    def __init__(self, config: ModelConfig, steps: list[dict[str, Any]] | None = None):
        self.settings = config
        self.steps = list(steps) if steps is not None else None
        self.calls = 0

    def update_config(self, **kwargs: Any) -> None:
        raise ValueError("The development scripted model has immutable settings")

    def get_config(self) -> dict[str, Any]:
        return {"model_id": self.settings.model_id}

    async def structured_output(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise RuntimeError("Use invoke_async structured output so generation hooks apply")
        yield  # pragma: no cover

    async def stream(
        self,
        messages: Any,
        tool_specs: Any = None,
        system_prompt: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        self.calls += 1
        if self.steps is not None:
            if not self.steps:
                raise ValueError("Development scripted transport exhausted")
            step = self.steps.pop(0)
        else:
            response = self._response(messages)
            if any(spec["name"] == "TeamResult" for spec in tool_specs or []):
                step = {"name": "TeamResult", "input": response}
            else:
                step = {"text": response.get("report_markdown") or response["summary"]}
        yield {"messageStart": {"role": "assistant"}}
        if "name" in step:
            yield {
                "contentBlockStart": {
                    "start": {
                        "toolUse": {
                            "toolUseId": "scripted-" + str(self.calls),
                            "name": step["name"],
                        }
                    }
                }
            }
            yield {
                "contentBlockDelta": {
                    "delta": {
                        "toolUse": {
                            "input": json.dumps(step.get("input", {})),
                        }
                    }
                }
            }
        elif "text" in step:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": step["text"]}}}
        else:
            raise ValueError("Development script requires a tool call or text step")
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use" if "name" in step else "end_turn"}}

    def _response(self, messages: Any) -> dict[str, Any]:
        response = self.settings.script
        if response is None:
            question = next(
                (
                    block["text"]
                    for message in messages
                    if message["role"] == "user"
                    for block in message["content"]
                    if "text" in block
                ),
                "Development fixture",
            )
            response = {
                "summary": question,
                "warnings": [
                    "Development scripted fixture; no live acceptance.",
                ],
            }
        if not isinstance(response, dict):
            raise ValueError("Thin scripted response must be a TeamResult object")
        return response


class DeepSeekOpenAIModel(InferenceOpenAIModel):
    """Carry provider-required reasoning opaquely in memory, outside native events.

    A model belongs to one agent invocation. Keys include the complete ordered
    assistant history (including tool-call IDs), so equal visible text in two
    turns cannot accidentally substitute another turn's opaque payload.
    """

    def __init__(self, *, telemetry: CompletionTelemetry | None = None, **kwargs: Any):
        super().__init__(**kwargs)
        self._opaque_turns: dict[str, str] = {}
        self.telemetry = telemetry

    @staticmethod
    def _public_identity(message: dict[str, Any]) -> dict[str, Any]:
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(block.get("text", "") for block in content)
        calls = []
        for call in message.get("tool_calls", []):
            function = call["function"]
            arguments = function.get("arguments", "{}")
            try:
                arguments = json.loads(arguments)
            except (ValueError, TypeError):
                # Strands converts malformed/empty tool JSON to {} before the
                # next turn. Mirror that public normalization for identity;
                # retain exact call IDs and ordered history ownership.
                arguments = {}
            calls.append({"id": call["id"], "name": function["name"], "arguments": arguments})
        return {"content": content, "tool_calls": calls}

    @staticmethod
    def _turn_key(history: list[dict[str, Any]]) -> str:
        return hashlib.sha256(
            json.dumps(history, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def format_request(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        request = super().format_request(*args, **kwargs)
        # Non-streaming keeps provider reasoning entirely out of Strands blocks,
        # callbacks, persisted transcripts, and debug formatting in the SDK.
        request["stream"] = False
        request.pop("stream_options", None)
        if self.telemetry is not None:
            self.telemetry.observe_request(request)
        history: list[dict[str, Any]] = []
        for message in request["messages"]:
            if message["role"] == "assistant":
                content = message.get("content")
                if isinstance(content, list):
                    message["content"] = "".join(block.get("text", "") for block in content)
                history.append(self._public_identity(message))
                key = self._turn_key(history)
                if key not in self._opaque_turns:
                    raise ValueError("DeepSeek assistant turn has no owned opaque transport state")
                message["reasoning_content"] = self._opaque_turns[key]
        return request

    def format_chunk(self, event: dict[str, Any], **kwargs: Any) -> Any:
        if self.telemetry is not None:
            self.telemetry.observe(event)
        return super().format_chunk(event, **kwargs)

    async def stream(
        self,
        messages: Any,
        tool_specs: Any = None,
        system_prompt: Any = None,
        *,
        tool_choice: Any = None,
        agent_metadata: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        request = self.format_request(
            messages, tool_specs, system_prompt, tool_choice, agent_metadata=agent_metadata
        )
        async with self._get_client() as client:
            response = await client.chat.completions.create(**request)
        message = response.choices[0].message
        public = message.model_dump(exclude={"reasoning_content", "reasoning"}, exclude_none=True)
        history = [
            self._public_identity(m) for m in request["messages"] if m["role"] == "assistant"
        ]
        history.append(self._public_identity(public))
        self._opaque_turns[self._turn_key(history)] = (
            getattr(message, "reasoning_content", None) or ""
        )
        # Remove opaque fields before the SDK converts the public response to events.
        safe_message = message.model_copy(update={"reasoning_content": None, "reasoning": None})
        safe_choice = response.choices[0].model_copy(update={"message": safe_message})
        safe_response = response.model_copy(update={"choices": [safe_choice]})
        for chunk in self._format_non_streaming_response(safe_response):
            yield chunk


def build_model(
    config: ModelConfig,
    *,
    scripted_steps: list[dict[str, Any]] | None = None,
    telemetry: CompletionTelemetry | None = None,
) -> tuple[Model, AsyncOpenAI | None]:
    """Create only the selected backend and return its owned client for run cleanup."""
    if config.provider == "scripted":
        return ScriptedModel(config, scripted_steps), None
    if not config.allow_inference_network:
        raise ValueError("Inference network requires explicit allow_inference_network=true")
    if not config.endpoint or not config.api_key_env:
        raise ValueError("An explicit inference endpoint and API-key reference are required")
    key = os.environ.get(config.api_key_env)
    if not key:
        raise ValueError("The configured inference API-key environment reference is unavailable")
    # A run owns these clients. Never follow provider redirects or inherit proxy credentials.
    client = AsyncOpenAI(
        base_url=config.endpoint,
        api_key=key,
        max_retries=0,
        timeout=config.timeout_seconds,
        http_client=httpx.AsyncClient(follow_redirects=False, trust_env=False),
    )
    params: dict[str, Any] = {"max_tokens": config.max_output_tokens}
    if config.temperature is not None:
        params["temperature"] = config.temperature
    if config.reasoning_effort is not None:
        params["reasoning_effort"] = config.reasoning_effort
    extra_body: dict[str, Any] = {}
    if config.include_reasoning is not None:
        extra_body["include_reasoning"] = config.include_reasoning
    if config.enable_thinking is not None:
        extra_body["chat_template_kwargs"] = {"enable_thinking": config.enable_thinking}
    if extra_body:
        params["extra_body"] = extra_body
    if (
        urlparse(config.endpoint).hostname == "api.deepseek.com"
        and config.model_id == "deepseek-flash"
    ):
        params.pop("temperature", None)
        params["extra_body"] = {"thinking": {"type": "enabled"}}
        return DeepSeekOpenAIModel(
            telemetry=telemetry, client=client, model_id=config.model_id, params=params
        ), client
    if telemetry is not None:
        return ObservedOpenAIModel(
            telemetry=telemetry, client=client, model_id=config.model_id, params=params
        ), client
    return InferenceOpenAIModel(
        client=client,
        model_id=config.model_id,
        params=params,
    ), client
