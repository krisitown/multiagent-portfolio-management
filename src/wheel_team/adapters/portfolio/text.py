"""Readable, lossless text transport; legacy snapshots remain accepted at ingress."""

from typing import Any

from wheel_team.types import TeamRequest


def _lines(value: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        rows: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                rows.append(f"{prefix}{key}:")
                rows.extend(_lines(item, indent + 2))
            else:
                rows.append(f"{prefix}{key}: {_scalar(item)}")
        return rows
    if isinstance(value, list):
        rows = []
        for index, item in enumerate(value, 1):
            rows.append(f"{prefix}Item {index}:")
            rows.extend(_lines(item, indent + 2))
        return rows
    return [prefix + _scalar(value)]


def _scalar(value: Any) -> str:
    if value is None:
        return "unknown (not supplied)"
    if value == []:
        return "empty collection (supplied)"
    if value == {}:
        return "empty mapping (supplied)"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def readable_portfolio(request: TeamRequest) -> str | None:
    """Prefer original text unchanged; render all legacy fields without inference."""
    if request.portfolio_text is not None:
        return request.portfolio_text
    if request.portfolio is None:
        return None
    return "\n".join(_lines(request.portfolio.model_dump(mode="json")))
