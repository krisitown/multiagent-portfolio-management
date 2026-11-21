"""Selected original material and operational context accounting; no verdict schema."""

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from wheel_team.vault import InvalidRecord, RecordNotFound


class EvidenceSelectionError(ValueError):
    """A selected reference is unavailable in the author's capability scope."""


def selected_evidence(
    refs: list[str], store: Any, private_inputs: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Read only requested originals, retaining all provenance when bodies coincide."""
    bodies: dict[str, dict[str, Any]] = {}
    for ref in dict.fromkeys(refs):
        try:
            if ref.startswith("input:"):
                if private_inputs is None or ref not in private_inputs:
                    raise EvidenceSelectionError("Selected reference unavailable or unauthorized")
                item = private_inputs[ref]
                body, metadata = item["body"], item["metadata"]
            else:
                record = store.get(ref)
                if "content" in record:
                    body = record["content"]
                    metadata = {key: value for key, value in record.items() if key != "content"}
                else:
                    body, metadata = record, {}
        except (InvalidRecord, RecordNotFound) as exc:
            raise EvidenceSelectionError("Selected reference unavailable or unauthorized") from exc
        identity = json.dumps(body, sort_keys=True, ensure_ascii=False, allow_nan=False)
        envelope = bodies.setdefault(identity, {"body": body, "aliases": [], "provenance": []})
        envelope["aliases"].append(ref)
        envelope["provenance"].append({"ref": ref, "metadata": metadata})
    return list(bodies.values())


class TokenCounter:
    """Count the complete chat request, or use an explicitly conservative byte bound."""

    def __init__(self, tokenizer: Any = None, identity: dict[str, Any] | None = None):
        self.tokenizer = tokenizer
        self.identity = identity or {"tokenizer": "unavailable"}

    @classmethod
    def local(cls, path: Path | None) -> "TokenCounter":
        if path is None:
            return cls()
        # No network, remote code, implicit model lookup, or downloads.
        try:
            import hashlib

            import transformers  # type: ignore[import-not-found]  # optional local runtime

            tokenizer = transformers.AutoTokenizer.from_pretrained(
                str(path), local_files_only=True, trust_remote_code=False
            )
            if not tokenizer.chat_template:
                raise ValueError("No local chat template")
            identity = {
                "path": str(path),
                "class": type(tokenizer).__name__,
                "transformers_version": transformers.__version__,
                "assets_sha256": {
                    name: hashlib.sha256((path / name).read_bytes()).hexdigest()
                    for name in (
                        "tokenizer.json",
                        "tokenizer_config.json",
                        "chat_template.jinja",
                        "vocab.json",
                        "merges.txt",
                        "config.json",
                    )
                    if (path / name).is_file()
                },
                "chat_template_sha256": hashlib.sha256(
                    json.dumps(tokenizer.chat_template, sort_keys=True).encode()
                ).hexdigest(),
            }
            return cls(tokenizer, identity)
        except (ImportError, OSError, ValueError):
            return cls(identity={"tokenizer": "unavailable", "local_path": str(path)})

    def measure(
        self, messages: list[dict[str, str]], *, output_tokens: int, context_limit: int
    ) -> dict[str, Any]:
        if self.tokenizer is not None:
            ids = self.tokenizer.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True
            )
            if isinstance(ids, Mapping):
                ids = ids["input_ids"]
            if hasattr(ids, "tolist"):
                ids = ids.tolist()
            if not isinstance(ids, list) or any(not isinstance(item, int) for item in ids):
                raise ValueError("Expected one flat chat-template token sequence")
            count, method, upper = len(ids), "local_tokenizer_chat_template", False
        else:
            # UTF-8 bytes conservatively cover byte/subword text tokens. Reserve ample
            # framing for this fixed two-message, zero-tool request. Never call this exact.
            count = len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 4096
            method, upper = "utf8_bytes_plus_4096_framing_upper_bound", True
        return {
            "input_tokens": count,
            "output_reserve": output_tokens,
            "context_limit": context_limit,
            "fits": count + output_tokens <= context_limit,
            "method": method,
            "is_upper_bound": upper,
            "tokenizer_identity": self.identity,
        }


def save_check(path: Path, value: dict[str, Any]) -> None:
    """Atomically retain private ordinary artifacts, including reservations before calls."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".check-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
