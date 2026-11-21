"""Presentation-only redaction; source artifacts are never rewritten."""

import json
import re
from typing import Any

_PRIVATE = re.compile(
    r"reasoning|thinking|chain.?of.?thought|authorization|api.?key|password|secret|credential|access.?token|refresh.?token",
    re.I,
)
_SECRET = re.compile(
    r"(?i)(\b(?:api[_-]?key|password|secret|access[_-]?token|refresh[_-]?token)"
    r"[\"']?\s*[=:]\s*)"
    r"(?:\"(?:\\.|[^\"\\])*(?:\"|$)|'(?:\\.|[^'\\])*(?:'|$)|[^\s,;}]+)"
)
_AUTH = re.compile(r"(?i)(\bauthorization[\"']?\s*[:=]\s*[\"']?)(?:Basic|Bearer)\s+[^\s\"'&,}]+")
_BEARER = re.compile(r"(?i)\bBearer\s+[\w.\-]+|\bsk-[A-Za-z0-9_-]{12,}")


def clean(value: Any, depth: int = 0) -> Any:
    if depth > 40:
        return "[depth limit]"
    if isinstance(value, dict):
        return {
            str(k): clean(v, depth + 1) for k, v in value.items() if not _PRIVATE.search(str(k))
        }
    if isinstance(value, list):
        return [clean(v, depth + 1) for v in value]
    if isinstance(value, str):
        # Tool content often wraps a JSON object inside a text block.
        if value.lstrip().startswith(("{", "[")):
            try:
                return json.dumps(clean(json.loads(value), depth + 1), ensure_ascii=False)
            except (ValueError, RecursionError):
                pass
        value = _SECRET.sub(lambda m: m.group(1) + "[redacted]", value)
        value = _AUTH.sub(lambda m: m.group(1) + "[redacted]", value)
        return _BEARER.sub("[redacted]", value)
    return value
