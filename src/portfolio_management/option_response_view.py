"""Lossless option tables at model input only; native returns remain untouched."""

import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from strands.types.content import Messages

_TABLE = "$option_table"
_NOTE = (
    "Lossless option response v1. Each $option_table represents an ordered array of objects. "
    "columns names the values in each row, in order; shared_fields_for_every_row applies "
    "to EVERY row (including null/unknown/error values). original_key_order restores each "
    "object's field order. Read row values together with shared fields; no values are omitted. "
    "All original response fields are under data. Quotes remain observations, not fills."
)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs) or _TABLE in result:
        raise ValueError("Ambiguous original object")
    return result


def _pack(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _pack(v) for k, v in value.items()}
    if not isinstance(value, list):
        return value
    plain = [_pack(v) for v in value]
    if (
        len(value) < 2
        or not all(isinstance(row, dict) for row in value)
        or not all(list(row) == list(value[0]) for row in value)
    ):
        return plain
    keys = list(value[0])
    # Serialized equality preserves bool/int/float distinctions and signed floating zero.
    shared = {
        k: _pack(value[0][k])
        for k in keys
        if all(_dump(row[k]) == _dump(value[0][k]) for row in value)
    }
    columns = [k for k in keys if k not in shared]
    table = {
        _TABLE: {
            "original_key_order": keys,
            "columns": columns,
            "shared_fields_for_every_row": shared,
            "rows": [[_pack(row[k]) for k in columns] for row in value],
        }
    }
    return table if len(_dump(table)) < len(_dump(plain)) else plain


def compact_option_text(text: str) -> str:
    """Return a smaller self-describing view, or the exact original on fallback.

    The caller restricts this to option tool responses. Missing/reordered row keys
    stay as objects; duplicate keys, reserved markers and nonfinite values fall back.
    String contents (including source prose) are never summarized or truncated.
    """
    try:
        value = json.loads(text, object_pairs_hook=_object)
        packed = _pack(value)
        serialized = _dump(packed)
        if '"$option_table":' not in serialized:
            return text
        view = _dump({"option_response_view": _NOTE, "data": packed})
        return view if len(view) < len(text) else text
    except (ValueError, TypeError, RecursionError):
        return text


def _eligible(tool_use: Mapping[str, Any]) -> bool:
    if tool_use.get("name") == "options_liquidity":
        return True
    if tool_use.get("name") != "market_query":
        return False
    inputs = tool_use.get("input")
    query = inputs.get("query") if isinstance(inputs, dict) else None
    if isinstance(query, str):
        try:
            query = json.loads(query)
        except ValueError:
            return False
    return isinstance(query, dict) and query.get("capability") == "option_chains"


def option_response_messages(messages: Messages) -> Messages:
    """Project eligible SDK text blocks, preserving native history and other tools."""
    eligible_ids = {
        block["toolUse"]["toolUseId"]
        for message in messages
        for block in message["content"]
        if "toolUse" in block and _eligible(block["toolUse"])
    }
    if not eligible_ids:
        return messages
    projected = deepcopy(messages)
    for message in projected:
        for block in message["content"]:
            result = block.get("toolResult")
            if (
                result is None
                or result["toolUseId"] not in eligible_ids
                or result.get("status") != "success"
            ):
                continue
            for content in result["content"]:
                if "text" in content:
                    content["text"] = compact_option_text(content["text"])
    return projected
