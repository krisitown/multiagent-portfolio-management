"""Private, content-agnostic Markdown storage and immutable original sources."""

import fcntl
import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, cast

from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool, ToolGenerator, ToolSpec, ToolUse


class ContinuityError(ValueError):
    def __init__(self, code: str, message: str, path: str | None = None, **extra: Any):
        self.body = {"error": {"code": code, "message": message, **extra}}
        if path is not None:
            self.body["error"]["path"] = path
        super().__init__(code + ": " + message)


def revision(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _flush_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _mkdir_durable(path: Path) -> None:
    """Persist each new directory entry before a successful file receipt."""
    if path.is_dir():
        _flush_directory(path.parent)
        return
    _mkdir_durable(path.parent)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        if not path.is_dir():
            raise
    _flush_directory(path.parent)


class ContinuityFiles:
    """One explicit private root; external concurrent editors are unsupported."""

    def __init__(self, root: Path, *, writable: bool = False):
        self.root = root.expanduser().absolute()
        if self.root.is_symlink():
            raise ContinuityError("INVALID_PATH", "Root must not be a symlink")
        _mkdir_durable(self.root)
        self.root = self.root.resolve()
        self.writable = writable
        self.read_refs: list[str] = []
        self.writes: list[dict[str, Any]] = []
        for name in ("vault", "sources", "history"):
            p = self.root / name
            if p.is_symlink():
                raise ContinuityError("INVALID_PATH", "Control directory is a symlink")
            _mkdir_durable(p)

    def _path(self, virtual: str) -> Path:
        p = PurePosixPath(virtual)
        if (
            not virtual
            or p.is_absolute()
            or "\\" in virtual
            or any(
                x in {"..", ".", ""} or x.startswith(".") for x in virtual.rstrip("/").split("/")
            )
            or p.parts[0] not in {"vault", "sources", "history"}
        ):
            raise ContinuityError("INVALID_PATH", "Invalid virtual path", virtual)
        target = self.root.joinpath(*p.parts)
        walk = self.root
        for part in p.parts:
            walk = walk / part
            if walk.is_symlink():
                raise ContinuityError("INVALID_PATH", "Symlinks are not permitted", virtual)
        if not target.resolve().is_relative_to(self.root):
            raise ContinuityError("INVALID_PATH", "Path leaves private root", virtual)
        return target

    @contextmanager
    def _lock(self) -> Iterator[None]:
        lock = self.root / ".writer.lock"
        if lock.is_symlink():
            raise ContinuityError("INVALID_PATH", "Writer lock is a symlink")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _atomic(self, target: Path, raw: bytes) -> None:
        _mkdir_durable(target.parent)
        fd, name = tempfile.mkstemp(prefix=".continuity-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, target)
            _flush_directory(target.parent)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _raw(self, virtual: str) -> bytes:
        try:
            raw = self._path(virtual).read_bytes()
            raw.decode("utf-8")
            return raw
        except ContinuityError:
            raise
        except FileNotFoundError as exc:
            raise ContinuityError("NOT_FOUND", "File is unavailable", virtual) from exc
        except UnicodeError as exc:
            raise ContinuityError("INVALID_ARGUMENT", "File is not UTF-8", virtual) from exc
        except OSError as exc:
            raise ContinuityError("IO_ERROR", "Unable to read file", virtual) from exc

    def _snapshot(self, virtual: str, raw: bytes) -> str:
        if not virtual.startswith("vault/"):
            return virtual
        name = "history/" + virtual.removeprefix("vault/") + "/" + revision(raw)
        dest = self._path(name)
        if dest.exists():
            if dest.read_bytes() != raw:
                raise ContinuityError("IO_ERROR", "Immutable snapshot differs", name)
        else:
            self._atomic(dest, raw)
        return name

    def _files(self, path: str) -> list[str]:
        target = self._path(path)
        if not target.exists():
            raise ContinuityError("NOT_FOUND", "Directory is unavailable", path)
        if not target.is_dir():
            raise ContinuityError("INVALID_PATH", "Directory required", path)
        result = []
        for p in sorted(target.rglob("*")):
            if any(part.startswith(".") for part in p.relative_to(target).parts):
                continue
            virtual = p.relative_to(self.root).as_posix()
            self._path(virtual)
            if p.is_file():
                self._raw(virtual)
                result.append(virtual)
        return result

    @staticmethod
    def _range(offset: int, limit: int) -> None:
        if (
            type(offset) is not int
            or type(limit) is not int
            or not 0 <= offset <= 2147483647
            or not 1 <= limit <= 500
        ):
            raise ContinuityError("INVALID_ARGUMENT", "Invalid pagination bounds")

    def continuity_list(
        self, path: str = "vault/", offset: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        """List permitted UTF-8 files recursively in path order; metadata is not a full read."""
        self._range(offset, limit)
        names = self._files(path)
        entries = []
        for name in names[offset : offset + limit]:
            raw = self._raw(name)
            entries.append({"path": name, "bytes": len(raw), "revision": revision(raw)})
        return {
            "files": entries,
            "next_offset": offset + limit if len(names) > offset + limit else None,
        }

    def continuity_read(
        self, path: str, start_line: int = 1, max_lines: int = 200
    ) -> dict[str, Any]:
        """Read exact lines and revision. Use immutable snapshot_path for further pages.

        Read all pages of a file before replacing it. Listing/search is not a full read.
        """
        if type(start_line) is not int or not 1 <= start_line <= 2147483647:
            raise ContinuityError("INVALID_ARGUMENT", "Invalid starting line")
        self._range(0, max_lines)
        raw = self._raw(path)
        try:
            with self._lock():
                snapshot = self._snapshot(path, raw)
        except OSError as exc:
            raise ContinuityError("IO_ERROR", "Unable to preserve snapshot", path) from exc
        lines = raw.decode("utf-8").splitlines(keepends=True)
        selected = lines[start_line - 1 : start_line - 1 + max_lines]
        content = ""
        for line in selected:
            if len(line.encode("utf-8")) > 65536:
                raise ContinuityError("TOO_LARGE", "Individual line exceeds read limit", path)
            if len((content + line).encode("utf-8")) > 65536:
                break
            content += line
        count = len(content.splitlines(keepends=True))
        end = start_line - 1 + count if count else 0
        if snapshot not in self.read_refs:
            self.read_refs.append(snapshot)
        return {
            "path": path,
            "revision": revision(raw),
            "snapshot_path": snapshot,
            "start_line": start_line,
            "end_line": end,
            "total_lines": len(lines),
            "content": content,
            "next_start_line": end + 1 if count and end < len(lines) else None,
        }

    def continuity_search(
        self, text: str, path: str = "vault/", offset: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        """Case-insensitive literal search. Excerpts (at most512 characters) are not full reads."""
        self._range(offset, limit)
        if not text:
            raise ContinuityError("INVALID_ARGUMENT", "Search text must not be empty")
        matches = []
        for name in self._files(path):
            raw = self._raw(name)
            hits = [
                (n, line)
                for n, line in enumerate(raw.decode("utf-8").splitlines(), 1)
                if text.casefold() in line.casefold()
            ]
            if hits:
                with self._lock():
                    snapshot = self._snapshot(name, raw)
                for n, line in hits:
                    matches.append(
                        {
                            "path": name,
                            "revision": revision(raw),
                            "snapshot_path": snapshot,
                            "line": n,
                            "excerpt": line[:512],
                        }
                    )
        return {
            "matches": matches[offset : offset + limit],
            "next_offset": offset + limit if len(matches) > offset + limit else None,
        }

    def continuity_write(
        self, path: str, content: str, expected_revision: str | None
    ) -> dict[str, Any]:
        """Atomically replace one vault Markdown file with complete exact UTF-8 content.

        Read every page before replacing; use the last-read revision, null only for creation.
        On conflict or uncertain outcome reread before reconciling; never retry blindly.
        Individual writes persist independently; interrupted work may be partially saved.
        """
        if not self.writable:
            raise ContinuityError("ACCESS_DENIED", "This consultation is read-only", path)
        dest = self._path(path)
        if not path.startswith("vault/") or dest.suffix != ".md":
            raise ContinuityError("ACCESS_DENIED", "Only vault Markdown is writable", path)
        try:
            raw = content.encode("utf-8")
            if len(raw) > 262144:
                raise ContinuityError("TOO_LARGE", "File exceeds256KiB write limit", path)
            with self._lock():
                previous = self._raw(path) if dest.exists() else None
                current = revision(previous) if previous is not None else None
                if expected_revision is None and current is not None:
                    raise ContinuityError(
                        "ALREADY_EXISTS",
                        "Creation requires an absent file",
                        path,
                        current_revision=current,
                    )
                if expected_revision is not None and current is None:
                    raise ContinuityError(
                        "NOT_FOUND", "Replacement requires an existing file", path
                    )
                if expected_revision is not None and current != expected_revision:
                    raise ContinuityError(
                        "REVISION_CONFLICT",
                        "Current file differs from supplied revision",
                        path,
                        current_revision=current,
                    )
                if previous is not None:
                    self._snapshot(path, previous)
                snapshot = self._snapshot(path, raw)
                changed = previous != raw
                if changed:
                    self._atomic(dest, raw)
                result = {
                    "path": path,
                    "revision": revision(raw),
                    "snapshot_path": snapshot,
                    "changed": changed,
                }
                self.writes.append(result)
                return result
        except (OSError, UnicodeError) as exc:
            raise ContinuityError("IO_ERROR", "Unable to save exact file", path) from exc

    def save_source(self, run_id: str, name: str, text: str) -> str:
        """Host-only narrow original-source adapter. Return a ref only after durable save."""
        raw = text.encode("utf-8")
        virtual = f"sources/{run_id}/{name}-{revision(raw)}.md"
        try:
            with self._lock():
                dest = self._path(virtual)
                if dest.exists():
                    if dest.read_bytes() != raw:
                        raise ContinuityError("IO_ERROR", "Immutable source differs", virtual)
                else:
                    self._atomic(dest, raw)
            return virtual
        except OSError as exc:
            raise ContinuityError("IO_ERROR", "Original source was not saved", virtual) from exc

    def _control(self, namespace: str, relative: str) -> Path:
        target = self.root / namespace / relative
        if not target.is_relative_to(self.root / namespace) or ".." in Path(relative).parts:
            raise ContinuityError("INVALID_PATH", "Invalid maintenance receipt path")
        current = self.root
        for part in target.relative_to(self.root).parts:
            current = current / part
            if current.is_symlink():
                raise ContinuityError("INVALID_PATH", "Symlink in maintenance receipt path")
        return target

    def save_maintenance(self, identity: str, receipt: dict[str, Any]) -> None:
        """Host operational metadata only; no financial status or automatic retry."""
        raw = (json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n").encode()
        try:
            with self._lock():
                dest = self._control(".maintenance", identity + ".json")
                archive = self._control(
                    ".maintenance-history", identity + "/" + revision(raw) + ".json"
                )
                if not archive.exists():
                    self._atomic(archive, raw)
                self._atomic(dest, raw)
        except OSError as exc:
            raise ContinuityError("IO_ERROR", "Maintenance receipt was not saved") from exc

    def unfinished_maintenance(self) -> list[dict[str, Any]]:
        """Return persisted interrupted invocation metadata after a process restart."""
        directory = self._control(".maintenance", "")
        receipts = []
        try:
            for p in sorted(directory.glob("*.json")):
                self._control(".maintenance", p.name)
                record = json.loads(p.read_text())
                if not record["completed"]:
                    receipts.append(record)
            return receipts
        except (OSError, ValueError, KeyError) as exc:
            raise ContinuityError("IO_ERROR", "Maintenance receipt is unavailable") from exc


def continuity_tools(
    files: ContinuityFiles, audit: list[dict[str, Any]], persist: Callable[[], None]
) -> dict[str, Any]:
    """Expose exact generic tool contracts and log only ordinary inputs/results."""
    from functools import wraps

    from strands import tool

    def string(description: str) -> dict[str, Any]:
        return {"type": "string", "description": description}

    def integer(default: int, minimum: int, maximum: int) -> dict[str, Any]:
        return {
            "type": "integer",
            "default": default,
            "minimum": minimum,
            "maximum": maximum,
        }

    path = string("Virtual path beneath vault/, sources/ or history/.")
    directory = {**path, "default": "vault/"}
    offset = integer(0, 0, 2147483647)
    definitions = {
        "continuity_list": (
            {"path": directory, "offset": offset, "limit": integer(100, 1, 500)},
            [],
        ),
        "continuity_read": (
            {
                "path": {**path, "minLength": 1},
                "start_line": integer(1, 1, 2147483647),
                "max_lines": integer(200, 1, 500),
            },
            ["path"],
        ),
        "continuity_search": (
            {
                "text": {**string("Nonempty case-insensitive literal text."), "minLength": 1},
                "path": directory,
                "offset": offset,
                "limit": integer(50, 1, 500),
            },
            ["text"],
        ),
        "continuity_write": (
            {
                "path": {**string("Relative vault/ Markdown path."), "minLength": 1},
                "content": string("Complete exact intended UTF-8 file contents."),
                "expected_revision": {
                    "type": ["string", "null"],
                    "description": "Last-read opaque revision; null only for creation.",
                },
            },
            ["path", "content", "expected_revision"],
        ),
    }
    result = {}
    for name, (properties, required) in definitions.items():
        if name == "continuity_write" and not files.writable:
            continue
        fn = getattr(files, name)

        def bind(fn: Callable[..., Any]) -> Callable[..., Any]:
            @wraps(fn)
            def invoke(*args: Any, **kwargs: Any) -> Any:
                import inspect

                bound = inspect.signature(fn).bind(*args, **kwargs)
                bound.apply_defaults()
                entry = {"tool": fn.__name__, "input": dict(bound.arguments)}
                audit.append(entry)
                persist()
                try:
                    value = fn(*args, **kwargs)
                    entry["result"] = value
                    return value
                except ContinuityError as exc:
                    value = {"status": "error", "content": [{"json": exc.body}]}
                    entry["result"] = value
                    return value
                except OSError:
                    value = {
                        "status": "error",
                        "content": [
                            {
                                "json": {
                                    "error": {
                                        "code": "IO_ERROR",
                                        "message": "Filesystem operation failed",
                                    }
                                }
                            }
                        ],
                    }
                    entry["result"] = value
                    return value
                finally:
                    persist()

            return invoke

        exposed = tool(name=name, description=fn.__doc__ or name)(bind(fn))
        exposed.tool_spec["inputSchema"]["json"] = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
        result[name] = ContinuityTool(exposed, exposed.tool_spec["inputSchema"]["json"])
    return result


# SDK function tools normally discard extra fields and coerce scalar types. These
# five private file/consultation contracts require exact input validation instead.


class ContinuityTool(AgentTool):
    """Strict scalar contract for the five private continuity tools only."""

    def __init__(self, wrapped: Any, schema: dict[str, Any]):
        super().__init__()
        self.wrapped = wrapped
        wrapped.tool_spec["inputSchema"]["json"] = schema

    @property
    def tool_name(self) -> str:
        return cast(str, self.wrapped.tool_name)

    @property
    def tool_spec(self) -> ToolSpec:
        return cast(ToolSpec, self.wrapped.tool_spec)

    @property
    def tool_type(self) -> str:
        return cast(str, self.wrapped.tool_type)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.wrapped(*args, **kwargs)

    async def stream(
        self, tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs: Any
    ) -> ToolGenerator:
        raw = tool_use.get("input", {})
        schema = self.tool_spec["inputSchema"]["json"]
        valid = (
            isinstance(raw, dict)
            and set(raw) <= set(schema["properties"])
            and set(schema["required"]) <= set(raw)
        )
        if valid:
            for name, value in raw.items():
                prop = schema["properties"][name]
                kinds = prop["type"] if isinstance(prop["type"], list) else [prop["type"]]
                kind = (
                    "null"
                    if value is None
                    else "integer"
                    if type(value) is int
                    else "string"
                    if isinstance(value, str)
                    else "invalid"
                )
                if kind not in kinds or ("enum" in prop and value not in prop["enum"]):
                    valid = False
                    break
                if kind == "integer" and not prop.get("minimum", value) <= value <= prop.get(
                    "maximum", value
                ):
                    valid = False
                    break
                if kind == "string" and len(value) < prop.get("minLength", 0):
                    valid = False
                    break
        if not valid:
            yield ToolResultEvent(
                {
                    "toolUseId": tool_use["toolUseId"],
                    "status": "error",
                    "content": [
                        {
                            "json": {
                                "error": {
                                    "code": "INVALID_ARGUMENT",
                                    "message": "Input does not match the private tool contract",
                                }
                            }
                        }
                    ],
                }
            )
            return
        async for event in self.wrapped.stream(tool_use, invocation_state, **kwargs):
            yield event
