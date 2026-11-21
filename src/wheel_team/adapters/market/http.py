"""Operator-configured read-only HTTP bridge with bounded shared fetches."""

import asyncio
import json
import math
import os
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from wheel_team.contracts.common import JsonValue
from wheel_team.contracts.evidence import MarketQuery, MarketResult

from .base import consumer_result, declared_capabilities, failure, query_identity

# Provider transport/authentication configuration is never a model query parameter.
_TRANSPORT_FIELDS = frozenset(
    {
        "endpoint",
        "base_url",
        "url",
        "api_key",
        "api_key_env",
        "headers",
        "credentials",
        "authorization",
        "transport",
        "method",
        "path",
    }
)


def _has_transport_configuration(value: JsonValue) -> bool:
    if isinstance(value, dict):
        return any(
            key.lower() in _TRANSPORT_FIELDS or _has_transport_configuration(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_has_transport_configuration(child) for child in value)
    return False


def _finite_json_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("response JSON contains a nonfinite number")
    return number


def _bridge_url(endpoint: str) -> str:
    try:
        parsed = urlsplit(endpoint)
        url = httpx.URL(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "?" in endpoint
            or "#" in endpoint
            or any(character.isspace() or ord(character) < 32 for character in endpoint)
            or not url.host
        ):
            raise ValueError
        return str(url).rstrip("/") + "/query"
    except (ValueError, httpx.InvalidURL):
        raise ValueError(
            "provider endpoint must be an absolute HTTP(S) URL without credentials"
        ) from None


class _BorrowedTransport(httpx.AsyncBaseTransport):
    """A caller-injected transport remains caller-owned across per-fetch clients."""

    def __init__(self, delegate: httpx.AsyncBaseTransport) -> None:
        self._delegate = delegate

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._delegate.handle_async_request(request)


@dataclass
class _Inflight:
    task: asyncio.Task[MarketResult]
    consumers: int = 0


class HttpMarketProvider:
    def __init__(
        self,
        *,
        endpoint: str,
        capabilities: Iterable[str],
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.5,
        api_key_env: str | None = None,
    ) -> None:
        self._url = _bridge_url(endpoint)
        self._capabilities = declared_capabilities(capabilities)
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("provider timeout must be finite and positive")
        if type(max_retries) is not int or max_retries < 0:
            raise ValueError("provider retry count must be a nonnegative integer")
        if (
            isinstance(retry_delay_seconds, bool)
            or not math.isfinite(retry_delay_seconds)
            or retry_delay_seconds < 0
        ):
            raise ValueError("provider retry delay must be finite and nonnegative")
        token = os.environ.get(api_key_env) if api_key_env else None
        if api_key_env is not None and (not api_key_env or not token):
            raise ValueError("configured provider credential is unavailable")
        # HTTPX encodes string headers as ASCII. Check without attempting an
        # encoding that would create a secret-bearing UnicodeEncodeError context.
        if token is not None and (
            not token.isascii() or any(ord(character) < 32 for character in token)
        ):
            raise ValueError("configured provider credential is invalid") from None
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._private_values = tuple(value for value in (api_key_env, token) if value)
        self._transport = transport
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._retry_delay = retry_delay_seconds
        self._inflight: dict[str, _Inflight] = {}

    def capabilities(self) -> list[str]:
        return list(self._capabilities)

    async def query(self, query: MarketQuery) -> MarketResult:
        # Detach mutable JSON before computing identity or scheduling the shared fetch.
        query = MarketQuery.model_validate(query.model_dump(mode="json"))
        if _has_transport_configuration(query.parameters):
            return failure(query, "invalid_data", "Market query contains provider configuration.")
        if query.capability not in self._capabilities:
            return failure(query, "unavailable", "Requested provider capability is unavailable.")
        try:
            identity = query_identity(query)
        except ValueError:
            return failure(query, "invalid_data", "Market query is not valid JSON data.")
        shared = self._inflight.get(identity)
        if shared is None or shared.task.done():
            shared = _Inflight(asyncio.create_task(self._fetch(query)))
            self._inflight[identity] = shared
        shared.consumers += 1
        try:
            result = await asyncio.shield(shared.task)
            return consumer_result(result, query.query_id)
        finally:
            shared.consumers -= 1
            if shared.task.done() or shared.consumers == 0:
                if self._inflight.get(identity) is shared:
                    del self._inflight[identity]
                if not shared.task.done():
                    shared.task.cancel()
                    # Fetch cleanup (including client closure) finishes before the last
                    # cancelled consumer exits; cancellation never cancels other consumers.
                    try:
                        await shared.task
                    except asyncio.CancelledError:
                        pass

    async def _fetch(self, query: MarketQuery) -> MarketResult:
        transport = _BorrowedTransport(self._transport) if self._transport else None
        async with httpx.AsyncClient(
            transport=transport,
            timeout=self._timeout,
            trust_env=False,
            follow_redirects=False,
            headers=self._headers,
        ) as client:
            for attempt in range(self._max_retries + 1):
                try:
                    async with asyncio.timeout(self._timeout):
                        response = await client.post(self._url, json=query.model_dump(mode="json"))
                    result = self._normalize_response(query, response)
                except (httpx.TransportError, TimeoutError):
                    result = failure(query, "retryable_error", "Market provider request failed.")
                if result.status != "retryable_error" or attempt == self._max_retries:
                    return result
                # One retry owner. Retry-After cannot force unbounded provider-controlled waits.
                await asyncio.sleep(self._retry_delay)
        raise AssertionError("unreachable provider retry state")

    def _normalize_response(self, query: MarketQuery, response: httpx.Response) -> MarketResult:
        code = response.status_code
        if code == 429 or code == 408 or 500 <= code <= 599:
            return failure(query, "retryable_error", "Market provider is temporarily unavailable.")
        if code == 404:
            return failure(query, "unavailable", "Requested provider resource is unavailable.")
        if not 200 <= code <= 299:
            return failure(query, "permanent_error", "Market provider rejected the request.")
        try:
            # Reject nonstandard constants and finite-looking exponent literals
            # that overflow a float before Pydantic can coerce them to null on output.
            data = json.loads(
                response.content,
                parse_float=_finite_json_number,
                parse_constant=_finite_json_number,
            )
            result = MarketResult.model_validate(data)
        except (ValidationError, ValueError):
            return failure(query, "invalid_data", "Market provider returned invalid data.")
        if result.query_id != query.query_id:
            return failure(query, "invalid_data", "Market provider returned a mismatched query.")
        # Reject leaked configured secrets rather than silently altering scientific evidence.
        if self._contains_private_value(result.model_dump(mode="json")):
            return failure(query, "invalid_data", "Market provider returned private configuration.")
        return result

    def _contains_private_value(self, value: JsonValue) -> bool:
        if isinstance(value, str):
            return any(private in value for private in self._private_values)
        if isinstance(value, dict):
            return any(
                self._contains_private_value(key) or self._contains_private_value(child)
                for key, child in value.items()
            )
        if isinstance(value, list):
            return any(self._contains_private_value(child) for child in value)
        return False
