"""Run-local immutable records with serialized, private, atomic JSON writes."""

import hashlib
import json
import os
import re
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from portfolio_management.contracts import (
    CalculationRecord,
    EvidenceRecord,
    ExpertReview,
    IdeaRevision,
    PortfolioSnapshot,
    QualityAssessment,
)


class InvalidRecord(ValueError):
    """A record, reference or local path violates the run boundary."""


class RecordConflict(InvalidRecord):
    """Different content attempted to replace an immutable identity."""


class RecordNotFound(LookupError):
    """The selected run has no record under this reference."""


_LOCKS: dict[Path, Any] = {}
_LOCKS_GUARD = threading.Lock()
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
MAX_RECORD_BYTES = 4 * 1024 * 1024
RecordKind = Literal["evidence", "calculation", "snapshot", "idea", "review", "quality"]


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise InvalidRecord("Expected a bounded logical identifier, not a path")
    return value


def _model(kind: str, record: dict[str, Any]) -> BaseModel:
    try:
        match kind:
            case "evidence":
                return EvidenceRecord.model_validate(record)
            case "calculation":
                return CalculationRecord.model_validate(record)
            case "snapshot":
                return PortfolioSnapshot.model_validate(record)
            case "idea":
                return IdeaRevision.model_validate(record)
            case "review":
                return ExpertReview.model_validate(record)
            case "quality":
                return QualityAssessment.model_validate(record)
            case _:
                raise InvalidRecord("Unknown record kind")
    except (ValidationError, TypeError, ValueError) as exc:
        raise InvalidRecord("Record does not satisfy its typed schema") from exc


def _reference(kind: str, data: dict[str, Any]) -> str:
    field = {
        "evidence": "evidence_id",
        "calculation": "calculation_id",
        "snapshot": "snapshot_id",
        "idea": "idea_id",
        "review": "review_id",
        "quality": "assessment_id",
    }[kind]
    identity = _identifier(data[field])
    revision = "@" + _identifier(data["revision_id"]) if kind == "idea" else ""
    return f"{kind}:{identity}{revision}"


def record_reference(kind: RecordKind, record: dict[str, Any]) -> str:
    """Return the canonical typed reference after validating the immutable identity."""
    normalized = _model(kind, record).model_dump(mode="json")
    return _reference(kind, normalized)


def _validate_ref(ref: str) -> str:
    if not isinstance(ref, str) or ":" not in ref:
        raise InvalidRecord("Expected a typed logical record reference")
    kind, identity = ref.split(":", 1)
    if kind not in {"evidence", "calculation", "snapshot", "idea", "review", "quality"}:
        raise InvalidRecord("Unknown reference kind")
    if kind == "idea":
        if identity.count("@") != 1:
            raise InvalidRecord("Idea references require an immutable revision")
        for part in identity.split("@"):
            _identifier(part)
    else:
        _identifier(identity)
    return ref


class EvidenceStore:
    """One run, thread-safe local writers; process-death recovery is not a service."""

    def __init__(self, root: Path):
        root = Path(root)
        if root.is_symlink():
            raise InvalidRecord("Record root must not be a symlink")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root = root.resolve()
        os.chmod(self.root, 0o700)
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(self.root, threading.RLock())

    def _path(self, ref: str) -> Path:
        _validate_ref(ref)
        if self.root.is_symlink() or not self.root.is_dir():
            raise InvalidRecord("Record root is unavailable or unsafe")
        path = self.root / (hashlib.sha256(ref.encode()).hexdigest() + ".json")
        if path.is_symlink():
            raise InvalidRecord("Record file must not be a symlink")
        return path

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise InvalidRecord("Record is not a regular file")
                raw = handle.read(MAX_RECORD_BYTES + 1)
            if len(raw) > MAX_RECORD_BYTES:
                raise InvalidRecord("Record exceeds size bound")
            data = json.loads(raw)
            if not isinstance(data, dict) or set(data) != {"kind", "ref", "record"}:
                raise InvalidRecord("Invalid record envelope")
            normalized = json.loads(_model(data["kind"], data["record"]).model_dump_json())
            if _reference(data["kind"], normalized) != data["ref"]:
                raise InvalidRecord("Stored reference conflicts with record identity")
            return data
        except FileNotFoundError as exc:
            raise RecordNotFound("Record is absent from this run") from exc
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise InvalidRecord("Stored record is unavailable or invalid") from exc

    def put(self, kind: str, record: dict[str, Any]) -> str:
        """Validate and persist an immutable record; duplicate identical writes are safe."""
        model = _model(kind, record)
        data = json.loads(model.model_dump_json())
        ref = _reference(kind, data)
        envelope = {"kind": kind, "ref": ref, "record": data}
        raw = json.dumps(envelope, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > MAX_RECORD_BYTES:
            raise InvalidRecord("Record exceeds size bound")
        with self._lock:
            path = self._path(ref)
            if path.exists():
                if self._read(path) != envelope:
                    raise RecordConflict("Immutable record identity has conflicting content")
                return ref
            descriptor, name = tempfile.mkstemp(prefix=".record-", dir=self.root)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        return ref

    def get(self, ref: str) -> dict[str, Any]:
        """Return a detached JSON payload under an exact logical reference."""
        with self._lock:
            envelope = self._read(self._path(ref))
            if envelope["ref"] != ref:
                raise InvalidRecord("Stored reference does not match requested identity")
            record: dict[str, Any] = envelope["record"]
            return record

    def find(self, kind: str, query: dict[str, Any]) -> list[dict[str, Any]]:
        """Match exact top-level fields within this run; query values never become paths."""
        if kind not in {"evidence", "calculation", "snapshot", "idea", "review", "quality"}:
            raise InvalidRecord("Unknown record kind")
        if not isinstance(query, dict) or len(query) > 32:
            raise InvalidRecord("Expected a bounded field query")
        with self._lock:
            rows = []
            for path in sorted(self.root.glob("*.json")):
                if path.is_symlink():
                    raise InvalidRecord("Record file must not be a symlink")
                envelope = self._read(path)
                if envelope["kind"] == kind and all(
                    envelope["record"].get(key) == value for key, value in query.items()
                ):
                    rows.append(envelope["record"])
            return rows
