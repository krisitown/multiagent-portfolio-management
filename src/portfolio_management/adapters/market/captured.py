"""Read-only completed market captures selected explicitly by the host."""

import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from portfolio_management.contracts import EvidenceRecord, MarketQuery, MarketResult

from .base import failure


def _read_verified_file(root: Path, relative_path: str, sha256: str) -> bytes:
    """Confine a producer reference to its capture and verify its original bytes."""
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("Capture file path must be relative and confined")
    selected_root = root.resolve(strict=True)
    selected_file = (selected_root / relative).resolve(strict=True)
    if not selected_file.is_relative_to(selected_root):
        raise ValueError("Capture file path escapes selected capture")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("Capture file hash must be SHA256")
    payload = selected_file.read_bytes()
    if hashlib.sha256(payload).hexdigest() != sha256:
        raise ValueError("Capture file hash mismatch")
    return payload


class CapturedMarketProvider:
    """Replay one explicitly selected completed capture without network or producer writes."""

    def __init__(self, index_path: str | Path) -> None:
        selected = Path(index_path).expanduser()
        root = selected.parent.resolve(strict=True)
        resolved = selected.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError("Capture index path escapes selected capture")
        index_bytes = resolved.read_bytes()
        index = json.loads(index_bytes)
        if not isinstance(index, dict) or index.get("schema_version") != 1:
            raise ValueError("Unsupported capture index")
        capture_id = index.get("capture_id")
        if not isinstance(capture_id, str) or selected.parent.name != capture_id:
            raise ValueError("Select an explicit immutable capture ID directory")
        if index.get("synthetic") is not False:
            raise ValueError("Captured market input must identify real observations")
        collection = json.loads(
            _read_verified_file(root, "collection.json", index["collection_sha256"])
        )
        if not isinstance(collection, dict) or collection.get("synthetic") is not False:
            raise ValueError("Captured collection must identify real observations")
        try:
            started = datetime.fromisoformat(collection["started_at"])
            finished = datetime.fromisoformat(collection["finished_at"])
            if started.utcoffset() is None or finished.utcoffset() is None or finished < started:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise ValueError(
                "Capture requires aware started_at and finished_at completion"
            ) from None
        entries, raw_sources = index.get("entries"), index.get("raw_sources")
        if not isinstance(entries, list) or not isinstance(raw_sources, list):
            raise ValueError("Capture requires indexed evidence and raw source hashes")
        for raw in raw_sources:
            _read_verified_file(root, raw["file"], raw["sha256"])
        self._entries: list[tuple[str, str, bool, EvidenceRecord]] = []
        ids: set[str] = set()
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("capability"), str)
                or not entry["capability"]
                or not isinstance(entry.get("instrument_id"), str)
                or not entry["instrument_id"]
                or type(entry.get("complete")) is not bool
            ):
                raise ValueError("Capture entry identity and completeness are required")
            record = EvidenceRecord.model_validate_json(
                _read_verified_file(root, entry["evidence_file"], entry["sha256"])
            )
            if (
                not isinstance(record.content, dict)
                or record.content.get("capture_id") != capture_id
                or record.content.get("instrument_id") != entry["instrument_id"]
            ):
                raise ValueError("Capture evidence identity differs from index")
            if record.evidence_id in ids:
                raise ValueError("Capture evidence IDs must be distinct")
            ids.add(record.evidence_id)
            self._entries.append(
                (entry["capability"], entry["instrument_id"], entry["complete"], record)
            )
        if not isinstance(index.get("scope"), dict):
            raise ValueError("Capture coverage scope is required")
        self._description = {
            "mode": "captured_replay",
            "provider": "completed_market_capture",
            "capture_id": capture_id,
            "index_sha256": hashlib.sha256(index_bytes).hexdigest(),
            "collection_sha256": index["collection_sha256"],
            "finished_at": collection["finished_at"],
            "coverage": index["scope"],
            "sources": sorted({record.source for _, _, _, record in self._entries}),
            "warnings": index.get("warnings", []),
            "query_parameters": "Use capability and company instrument_ids; no parameters/as_of",
        }

    def capabilities(self) -> list[str]:
        return list(dict.fromkeys(capability for capability, _, _, _ in self._entries))

    def describe(self) -> dict[str, Any]:
        return copy.deepcopy(self._description)

    async def query(self, query: MarketQuery) -> MarketResult:
        if query.capability not in self.capabilities():
            return failure(query, "unavailable", "Capability was not captured")
        if query.parameters or query.as_of is not None or query.limit > 1000:
            return failure(
                query,
                "invalid_data",
                "Replay accepts company instrument_ids; no parameters/as_of; limit <=1000",
            )
        selected = [
            entry
            for entry in self._entries
            if entry[0] == query.capability
            and (not query.instrument_ids or entry[1] in query.instrument_ids)
        ]
        if not selected:
            return failure(
                query, "unavailable", "No captured observation matches the requested company IDs"
            )
        try:
            offset = 0 if query.cursor is None else int(query.cursor)
            if (
                offset < 0
                or offset >= len(selected)
                or (query.cursor is not None and str(offset) != query.cursor)
            ):
                raise ValueError
        except ValueError:
            return failure(query, "invalid_data", "Invalid capture page cursor")
        page = selected[offset : offset + query.limit]
        following = offset + len(page)
        missing = set(query.instrument_ids) - {entry[1] for entry in selected}
        return MarketResult(
            query_id=query.query_id,
            status="success",
            evidence=tuple(
                EvidenceRecord.model_validate_json(entry[3].model_dump_json()) for entry in page
            ),
            next_cursor=str(following) if following < len(selected) else None,
            complete=following == len(selected)
            and not missing
            and all(entry[2] for entry in selected),
            reason="Captured replay; limited source coverage. "
            + " ".join(self._description["warnings"])
            + (" Missing company IDs: " + ", ".join(sorted(missing)) if missing else ""),
        )
