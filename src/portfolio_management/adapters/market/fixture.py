"""Validated synthetic market pages; a missing fixture never uses the network."""

import json
from collections.abc import Iterable
from pathlib import Path

from portfolio_management.contracts.evidence import MarketQuery, MarketResult

from .base import consumer_result, declared_capabilities, failure, query_identity


class FixtureMarketProvider:
    def __init__(
        self, examples_directory: str | Path, *, capabilities: Iterable[str] | None = None
    ) -> None:
        directory = Path(examples_directory)
        requests = json.loads((directory / "requests.json").read_text(encoding="utf-8"))
        responses = json.loads((directory / "responses.json").read_text(encoding="utf-8"))
        if requests.get("synthetic") is not True or responses.get("synthetic") is not True:
            raise ValueError("market fixtures must be explicitly synthetic")
        self._capabilities = declared_capabilities(
            requests["capabilities"] if capabilities is None else capabilities
        )
        request_pages = requests["requests"]
        response_pages = responses["responses"]
        if request_pages.keys() != response_pages.keys():
            raise ValueError("each market fixture request requires a matching response")
        self._pages: dict[str, MarketResult] = {}
        for name, request in request_pages.items():
            query = MarketQuery.model_validate(request)
            result = MarketResult.model_validate(response_pages[name])
            if query.query_id != result.query_id:
                raise ValueError("market fixture response has a mismatched query identifier")
            identity = query_identity(query)
            if identity in self._pages:
                raise ValueError("duplicate semantic market fixture request")
            self._pages[identity] = result

    def capabilities(self) -> list[str]:
        return list(self._capabilities)

    async def query(self, query: MarketQuery) -> MarketResult:
        if query.capability not in self._capabilities:
            return failure(query, "unavailable", "Requested provider capability is unavailable.")
        try:
            identity = query_identity(query)
        except ValueError:
            return failure(query, "invalid_data", "Market query is not valid JSON data.")
        result = self._pages.get(identity)
        if result is None:
            return failure(query, "unavailable", "No synthetic fixture matches this market query.")
        return consumer_result(result, query.query_id)
