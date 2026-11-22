"""Opt-in public-text sidecar. Model execution never waits for observer IO.

Only the SDK's ordinary text delta is accepted; reasoning/tool bodies are ignored.
One bounded queue and daemon writer per invocation, capped at 1 MiB visible text.
"""

import json
import os
import queue
import re
import stat
import threading
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from strands.hooks import HookProvider
from strands.hooks.events import (
    AfterInvocationEvent,
    AfterModelCallEvent,
    BeforeInvocationEvent,
    BeforeModelCallEvent,
)

from .privacy import clean

TEXT_LIMIT = 1024 * 1024
CHUNK_LIMIT = 8192
PROMPT_LIMIT = 256 * 1024
PROMPT_BUDGET = 4 * TEXT_LIMIT


class PublicOutputObserver(HookProvider):
    def __init__(self, context: Any, role: str, *, queue_size: int = 64):
        self.path = context.run_dir / "public-output.jsonl"
        self.context = context
        self.role = role
        self.invocation_id = context.invocation_id
        self.generation: int | None = None
        self._prompt_remaining = PROMPT_BUDGET
        self._prompt_sequence = 0
        self._prompt_session = uuid4().hex
        self._last_system = None
        self.dropped_events = 0
        self.io_failures = 0
        self._queue: queue.Queue[tuple[str, int | None, str]] = queue.Queue(maxsize=queue_size)
        self._closing = threading.Event()
        self._muted = False
        self._worker = threading.Thread(target=self._run, name="public-output", daemon=True)
        self._worker.start()

    def register_hooks(self, registry: Any, **kwargs: Any) -> None:
        registry.add_callback(BeforeInvocationEvent, self.before_invocation)
        registry.add_callback(BeforeModelCallEvent, self.before_model)
        registry.add_callback(AfterModelCallEvent, self.after_model)
        registry.add_callback(AfterInvocationEvent, self.after_invocation)

    def _put(self, kind: str, text: str = "") -> None:
        try:
            if self._muted:
                self.dropped_events += 1
                return
            if kind != "prompt" and len(text) > TEXT_LIMIT:
                self.dropped_events += 1
                text = text[:TEXT_LIMIT]
            self._queue.put_nowait((kind, self.generation, text))
        except Exception:
            self.dropped_events += 1

    def before_invocation(self, event: Any) -> None:
        try:
            if self._closing.is_set():
                # A previous blocked writer owns the sole writer slot. Lose observation,
                # never grow threads or wait for it from model execution.
                if self._worker.is_alive():
                    self._muted = True
                    self.dropped_events += 1
                    return
                self._muted = False
                self._queue = queue.Queue(maxsize=self._queue.maxsize)
                self._closing = threading.Event()
                self._worker = threading.Thread(target=self._run, name="public-output", daemon=True)
                self._worker.start()
            size = sum(
                len(b["text"])
                for m in event.messages or []
                for b in m.get("content", [])
                if isinstance(b.get("text"), str)
            )
            self.generation = None
            self._put("invocation", str(size))
            self._prompt_remaining = PROMPT_BUDGET
            self._prompt_session = uuid4().hex
            self._last_system = None
            for message in (event.messages or [])[:32]:
                if message.get("role") not in ("user", "assistant", "system"):
                    continue
                blocks = [
                    b["text"]
                    for b in message.get("content", [])
                    if isinstance(b, dict) and set(b) == {"text"} and isinstance(b["text"], str)
                ]
                if blocks:
                    chunks, remaining = [], PROMPT_LIMIT
                    for block in blocks:
                        chunks.append(block[:remaining])
                        remaining -= len(chunks[-1])
                        if remaining == 0:
                            break
                    self._prompt(
                        message["role"], "".join(chunks), original_chars=sum(map(len, blocks))
                    )
            if len(event.messages or []) > 32:
                self.dropped_events += 1
        except Exception:
            self.dropped_events += 1

    def _prompt(self, kind: str, text: str, *, original_chars: int | None = None) -> None:
        if self._muted:
            return
        if self._prompt_remaining <= 0:
            self.dropped_events += 1
            return
        size = len(text) if original_chars is None else original_chars
        visible = text[: min(PROMPT_LIMIT, self._prompt_remaining)]
        self._prompt_remaining -= len(visible)
        self._prompt_sequence += 1
        self._put(
            "prompt",
            json.dumps(
                {
                    "prompt_kind": kind,
                    "prompt_id": f"{self._prompt_session}:{self._prompt_sequence}",
                    "at": datetime.now(UTC).isoformat(),
                    "text": visible,
                    "text_chars": size,
                    "truncated": len(visible) < size,
                },
                ensure_ascii=False,
            ),
        )

    def before_model(self, event: Any) -> None:
        try:
            self.generation = self.context.budget.generations
            self._put("start")
            system = getattr(getattr(event, "agent", None), "system_prompt", None)
            if isinstance(system, str):
                visible = system[:PROMPT_LIMIT]
                if visible != self._last_system:
                    self._prompt("system", visible, original_chars=len(system))
                    self._last_system = visible
        except Exception:
            self.dropped_events += 1

    def __call__(self, **event: Any) -> None:
        try:
            delta = event.get("delta")
            if (
                not event.get("reasoning")
                and isinstance(delta, dict)
                and set(delta) == {"text"}
                and isinstance(delta["text"], str)
            ):
                text = delta["text"]
                if len(text) > CHUNK_LIMIT:
                    self.dropped_events += 1
                    text = text[:CHUNK_LIMIT]
                self._put("delta", text)
        except Exception:
            self.dropped_events += 1

    def after_model(self, event: Any) -> None:
        try:
            if event.stop_response is not None:
                content = event.stop_response.message.get("content", [])
                text = "".join(
                    b["text"]
                    for b in content
                    if isinstance(b, dict)
                    and isinstance(b.get("text"), str)
                    and "reasoningContent" not in b
                )
                self._put("final", text)
        except Exception:
            self.dropped_events += 1

    def after_invocation(self, event: Any) -> None:
        self.close()

    def close(self, *, wait: float = 0) -> None:
        self._closing.set()
        if wait:
            self._worker.join(timeout=wait)

    def _write(self, row: dict[str, Any]) -> None:
        descriptor = os.open(
            self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
        )
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise OSError("Sidecar must be a regular file")
            os.fchmod(handle.fileno(), 0o600)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _run(self) -> None:
        work_queue, closing = self._queue, self._closing
        generation = None
        text = ""
        dirty = False
        sequence = 0
        last_write = 0.0

        def emit(final: bool = False) -> None:
            nonlocal sequence, dirty, last_write
            # Hold the unfinished trailing token so credentials split over chunks never leak.
            visible = text if final else re.sub(r"\S+$", "", text)
            sequence += 1
            row = {
                "event": "public_text",
                "at": datetime.now(UTC).isoformat(),
                "role": self.role,
                "invocation_id": self.invocation_id,
                "generation": generation,
                "sequence": sequence,
                "replace": True,
                "text": clean(visible),
                "final": final,
                "dropped_events": self.dropped_events,
                "io_failures": self.io_failures,
            }
            try:
                self._write(row)
            except Exception:
                self.io_failures += 1
            dirty = False
            last_write = time.monotonic()

        while not closing.is_set() or not work_queue.empty():
            try:
                kind, number, chunk = work_queue.get(timeout=0.1)
            except queue.Empty:
                if dirty:
                    emit()
                continue
            try:
                if kind == "prompt":
                    self._write(
                        clean(
                            {
                                "event": "public_prompt",
                                "role": self.role,
                                "invocation_id": self.invocation_id,
                                "generation": number,
                                **json.loads(chunk),
                            }
                        )
                    )
                    continue
                if kind == "invocation":
                    self._write(
                        {
                            "event": "invocation_started",
                            "at": datetime.now(UTC).isoformat(),
                            "role": self.role,
                            "invocation_id": self.invocation_id,
                            "prompt_chars": int(chunk),
                        }
                    )
                    continue
                if kind == "start" or number != generation:
                    if dirty:
                        emit()
                    generation, text, sequence = number, "", 0
                if kind == "delta":
                    available = TEXT_LIMIT - len(text)
                    if len(chunk) > available:
                        self.dropped_events += 1
                    text += chunk[:available]
                    dirty = True
                    if time.monotonic() - last_write >= 0.15:
                        emit()
                elif kind == "final":
                    text = chunk
                    emit(final=True)
            except Exception:
                self.io_failures += 1
        if dirty:
            emit()
        try:
            self._write(
                {
                    "event": "observer_health",
                    "at": datetime.now(UTC).isoformat(),
                    "role": self.role,
                    "invocation_id": self.invocation_id,
                    "dropped_events": self.dropped_events,
                    "io_failures": self.io_failures,
                }
            )
        except Exception:
            self.io_failures += 1


def optional_observer(context: Any, role: str) -> PublicOutputObserver | None:
    if os.environ.get("WHEEL_TEAM_PUBLIC_OUTPUT") != "1":
        return None
    try:
        return PublicOutputObserver(context, role)
    except Exception:
        return None
