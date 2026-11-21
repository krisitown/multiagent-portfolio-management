"""Bounded incremental reads of fixed, allowlisted private run artifacts."""

import base64
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from .privacy import clean

LOGS = ("trace.jsonl", "public-output.jsonl")
ARTIFACTS = {
    "manifest.json": "artifact_manifest",
    "research-results.json": "artifact_research",
    "expert-results.json": "artifact_expert",
    "report.md": "artifact_report",
}
FIELDS = frozenset(
    {
        "event",
        "at",
        "role",
        "invocation_id",
        "generation",
        "elapsed_seconds",
        "status",
        "usage",
        "stop_reason",
        "error_category",
        "name",
        "call_id",
        "inputs",
        "result",
        "job_id",
        "outcome",
        "completion_reason",
        "generations",
        "expert_consultations",
        "text",
        "sequence",
        "final",
        "dropped_events",
        "io_failures",
        "replace",
        "boundary",
        "text_chars",
        "text_sha256",
        "prompt_chars",
    }
)
EVENTS = frozenset(
    {
        "run_started",
        "run_completed",
        "run_failed",
        "model_generation_started",
        "model_generation_finished",
        "tool_call_started",
        "tool_call_finished",
        "analyst_completed",
        "analyst_terminal_started",
        "lead_synthesis_started",
        "unvalidated_lead_draft_saved",
        "public_text",
        "observer_health",
        "invocation_started",
        "invocation_finished",
    }
)
MAX_READ = 4 * 1024 * 1024
MAX_ARTIFACT = 16 * 1024 * 1024


class RunReader:
    def __init__(self, run_dir: Path):
        root = Path(run_dir).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Run directory required")
        native = root / "observation" / "native"
        if not (root / "trace.jsonl").exists() and native.is_dir():
            candidates = [
                p for p in native.iterdir() if p.is_dir() and (p / "trace.jsonl").is_file()
            ]
            if len(candidates) != 1:
                raise ValueError("Pass one native run directory explicitly")
            candidate = candidates[0]
            if candidate.resolve().is_relative_to(root) and not candidate.is_symlink():
                root = candidate.resolve()
            else:
                raise ValueError("Run must remain inside configured directory")
        self.run_dir = root

    def _open(self, name: str) -> Any:
        if name not in (*LOGS, *ARTIFACTS):
            raise ValueError("Artifact is not allowlisted")
        path = self.run_dir / name
        if path.is_symlink() or not path.resolve().is_relative_to(self.run_dir):
            raise OSError("Unsafe artifact")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise OSError("Artifact must be a regular file")
        return os.fdopen(fd, "rb")

    @staticmethod
    def _signature(handle: Any, offset: int) -> str:
        # Verify all consumed bytes, including middle-of-file same-inode rewrites.
        # Stream fixed-size chunks so integrity checking never buffers the log body.
        handle.seek(0)
        digest = hashlib.sha256()
        remaining = offset
        while remaining:
            block = handle.read(min(remaining, 256 * 1024))
            if not block:
                break
            digest.update(block)
            remaining -= len(block)
        return digest.hexdigest()

    def poll(self, cursor: str | None) -> dict[str, Any]:
        prior: dict[str, Any] = {}
        if cursor:
            try:
                if len(cursor) > 12000:
                    raise ValueError
                prior = json.loads(base64.urlsafe_b64decode(cursor))
                if not isinstance(prior, dict) or prior.get("version") != 1:
                    raise ValueError
                if not isinstance(prior.get("files"), dict):
                    raise ValueError
            except (ValueError, TypeError):
                raise ValueError("Invalid event cursor") from None
        positions = prior.get("files", {})
        opened = {}
        warnings = []
        reset = False
        try:
            for name in LOGS:
                try:
                    handle = self._open(name)
                except OSError:
                    if name in positions:
                        reset = True
                    continue
                opened[name] = handle
                info = os.fstat(handle.fileno())
                previous = positions.get(name, {})
                offset = previous.get("offset", 0)
                if not isinstance(offset, int) or offset < 0:
                    raise ValueError("Invalid event cursor")
                identity = [info.st_dev, info.st_ino]
                if previous and (
                    previous.get("identity") != identity
                    or info.st_size < offset
                    or previous.get("signature") != self._signature(handle, offset)
                ):
                    reset = True
            if reset:
                positions = {}
            next_files: dict[str, Any] = {}
            events = []
            more = False
            for name, handle in opened.items():
                previous = positions.get(name, {})
                offset = previous.get("offset", 0)
                handle.seek(offset)
                data = handle.read(MAX_READ)
                end = data.rfind(b"\n") + 1
                if end == 0 and len(data) == MAX_READ:
                    # Discard an over-limit line through its next newline, bounded per poll.
                    # Keep an explicit skip flag so a line suffix cannot become a fake event.
                    warnings.append(f"{name}: over-limit line skipped")
                    next_files[name] = {"skip": True}
                    consumed = len(data)
                else:
                    consumed = end
                    lines = data[:end].splitlines()
                    if previous.get("skip") and lines:
                        lines = lines[1:]
                    for line in lines:
                        try:
                            value = json.loads(line)
                            if isinstance(value, dict) and value.get("event") in EVENTS:
                                events.append(
                                    clean({k: v for k, v in value.items() if k in FIELDS})
                                )
                        except (ValueError, RecursionError):
                            warnings.append(f"{name}: malformed complete line skipped")
                    next_files[name] = {"skip": bool(previous.get("skip")) and not end}
                offset += consumed
                info = os.fstat(handle.fileno())
                more = more or info.st_size - offset > len(data) - consumed
                next_files[name].update(
                    identity=[info.st_dev, info.st_ino],
                    offset=offset,
                    signature=self._signature(handle, offset),
                )
            for name, kind in ARTIFACTS.items():
                try:
                    with self._open(name) as handle:
                        info = os.fstat(handle.fileno())
                        fingerprint = [info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size]
                        next_files[name] = fingerprint
                        if positions.get(name) == fingerprint:
                            continue
                        raw = handle.read(MAX_ARTIFACT + 1)
                    if len(raw) > MAX_ARTIFACT:
                        warnings.append(f"{name}: artifact exceeds presentation limit")
                        continue
                    if kind == "artifact_report":
                        events.append(
                            {
                                "event": kind,
                                "text": clean(raw.decode("utf-8")),
                                "sha256": hashlib.sha256(raw).hexdigest(),
                                "bytes": len(raw),
                            }
                        )
                    else:
                        value = json.loads(raw)
                        if kind == "artifact_manifest":
                            value = {
                                k: value[k]
                                for k in (
                                    "run_id",
                                    "started_at",
                                    "completed_at",
                                    "completion_reason",
                                    "limits",
                                    "model_id",
                                )
                                if k in value
                            }
                        events.append({"event": kind, "results": clean(value)})
                except FileNotFoundError:
                    pass
                except (OSError, ValueError, TypeError, RecursionError):
                    warnings.append(f"{name}: unavailable or being written")
                    next_files.pop(name, None)  # retry an incomplete artifact on next poll
            token = base64.urlsafe_b64encode(
                json.dumps({"version": 1, "files": next_files}).encode()
            ).decode()
            return {
                "cursor": token,
                "events": events,
                "reset": reset,
                "warnings": warnings,
                "more": more,
            }
        finally:
            for handle in opened.values():
                handle.close()
