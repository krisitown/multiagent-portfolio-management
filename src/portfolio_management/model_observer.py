"""OpenAI request compatibility and whitelist-only native completion counters."""

import copy
import hashlib
import json
import re
from typing import Any

from strands.models.openai import OpenAIModel
from strands.types.streaming import StreamEvent


def _numbers(value: Any, names: tuple[str, ...]) -> dict[str, int]:
    return {
        name: number
        for name in names
        if type(number := getattr(value, name, None)) is int and number >= 0
    }


class CompletionTelemetry:
    """One probe's public request identity and provider-reported completion metadata."""

    def __init__(self) -> None:
        self.observed = False
        self.finish_reason: str | None = None
        self.usage: dict[str, Any] | None = None
        self.visible_text_chars = 0
        self.tool_argument_chars: dict[str, int] = {}
        self._tool_names: dict[int, str] = {}
        self.formatted_input_sha256: str | None = None
        self.request_controls: dict[str, Any] | None = None
        self.model_id: str | None = None

    def observe_request(self, request: dict[str, Any]) -> None:
        selected = {
            key: request[key] for key in ("messages", "tools", "tool_choice") if key in request
        }
        self.formatted_input_sha256 = hashlib.sha256(
            json.dumps(selected, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        self.model_id = request.get("model") if isinstance(request.get("model"), str) else None
        controls: dict[str, Any] = {}
        if type(request.get("max_tokens")) is int:
            controls["max_tokens"] = request["max_tokens"]
        if isinstance(request.get("reasoning_effort"), str) and request["reasoning_effort"] in {
            "low",
            "medium",
            "high",
            "xhigh",
        }:
            controls["reasoning_effort"] = request["reasoning_effort"]
        extra = request.get("extra_body") or {}
        body: dict[str, Any] = {}
        if type(extra.get("include_reasoning")) is bool:
            body["include_reasoning"] = extra["include_reasoning"]
        template = extra.get("chat_template_kwargs") or {}
        if type(template.get("enable_thinking")) is bool:
            body["chat_template_kwargs"] = {"enable_thinking": template["enable_thinking"]}
        if body:
            controls["extra_body"] = body
        self.request_controls = controls

    def observe(self, event: dict[str, Any]) -> None:
        self.observed = True
        kind, data = event.get("chunk_type"), event.get("data")
        if kind == "message_stop":
            self.finish_reason = (
                data
                if isinstance(data, str)
                and data
                in {"stop", "length", "tool_calls", "content_filter", "function_call", "end_turn"}
                else None
            )
        elif kind == "metadata":
            usage: dict[str, Any] = _numbers(
                data, ("prompt_tokens", "completion_tokens", "total_tokens")
            )
            for field, names in (
                (
                    "completion_tokens_details",
                    (
                        "reasoning_tokens",
                        "audio_tokens",
                        "accepted_prediction_tokens",
                        "rejected_prediction_tokens",
                    ),
                ),
                ("prompt_tokens_details", ("cached_tokens", "cache_write_tokens", "audio_tokens")),
            ):
                details = _numbers(getattr(data, field, None), names)
                if details:
                    usage[field] = details
            self.usage = usage
        elif kind == "content_delta" and event.get("data_type") == "text":
            if isinstance(data, str):
                self.visible_text_chars += len(data)
        elif kind in {"content_start", "content_delta"} and event.get("data_type") == "tool":
            function = getattr(data, "function", None)
            index = getattr(data, "index", 0)
            name = getattr(function, "name", None)
            if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
                self._tool_names[index] = name
            name = self._tool_names.get(index, "unknown_tool")
            if kind == "content_delta" and isinstance(
                arguments := getattr(function, "arguments", None), str
            ):
                self.tool_argument_chars[name] = self.tool_argument_chars.get(name, 0) + len(
                    arguments
                )
        # reasoning_content is intentionally neither counted nor retained.

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(
            {
                "observed": self.observed,
                "finish_reason": self.finish_reason,
                "usage": self.usage,
                "visible_text_chars": self.visible_text_chars if self.observed else None,
                "tool_argument_chars": self.tool_argument_chars if self.observed else None,
                "formatted_input_sha256": self.formatted_input_sha256,
                "request_controls": self.request_controls,
                "model_id": self.model_id,
            }
        )


class InferenceOpenAIModel(OpenAIModel):
    """Omit the SDK's empty tools array for OpenAI-compatible servers."""

    def format_request(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        request = super().format_request(*args, **kwargs)
        if request.get("tools") == []:
            del request["tools"]
        return request


class ObservedOpenAIModel(InferenceOpenAIModel):
    def __init__(self, *, telemetry: CompletionTelemetry, **kwargs: Any) -> None:
        self.telemetry = telemetry
        super().__init__(**kwargs)

    def format_request(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        request = super().format_request(*args, **kwargs)
        self.telemetry.observe_request(request)
        return request

    def format_chunk(self, event: dict[str, Any], **kwargs: Any) -> StreamEvent:
        self.telemetry.observe(event)
        return super().format_chunk(event, **kwargs)
