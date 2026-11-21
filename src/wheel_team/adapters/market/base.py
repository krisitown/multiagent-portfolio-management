"""Replaceable read-only market boundary; no provider capability is assumed."""

import json
from collections.abc import Iterable
from typing import Protocol

from wheel_team.contracts.evidence import MarketQuery, MarketResult, ResultState


class MarketProvider(Protocol):
    def capabilities(self) -> list[str]:
        """Return operator-declared capabilities, not a guarantee of complete coverage."""
        ...

    async def query(self, query: MarketQuery) -> MarketResult:
        """Return one page with explicit availability, time, coverage and provenance."""
        ...


def query_identity(query: MarketQuery) -> str:
    """Include all semantic fields, excluding only the consumer correlation ID."""
    data = query.model_dump(mode="json", exclude={"query_id"})
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)


def declared_capabilities(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, str):
        raise ValueError("capabilities must be a collection of nonempty strings")
    result = tuple(values)
    if any(not isinstance(value, str) or not value.strip() for value in result):
        raise ValueError("capabilities must be a collection of nonempty strings")
    if len(set(result)) != len(result):
        raise ValueError("capabilities must be distinct")
    return result


def failure(query: MarketQuery, status: ResultState, reason: str) -> MarketResult:
    """Never copy raw transport exceptions, credentials, or provider error bodies."""
    return MarketResult(query_id=query.query_id, status=status, complete=False, reason=reason)


def consumer_result(result: MarketResult, query_id: str) -> MarketResult:
    """Public models freeze fields, but arbitrary JSON must also be detached."""
    data = result.model_dump(mode="json")
    data["query_id"] = query_id
    return MarketResult.model_validate(data)
