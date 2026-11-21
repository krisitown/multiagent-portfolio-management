"""Validate indexed Markdown using confined, no-follow descriptor reads and hashes."""

import hashlib
import os
import stat
from pathlib import Path, PurePosixPath

from wheel_team.contracts import KnowledgeManifest, KnowledgeModule

MAX_MODULE_BYTES = 1048576


def read_module(manifest_path: Path, module: KnowledgeModule) -> str:
    path = PurePosixPath(module.path)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in module.path
        or path.suffix != ".md"
        or not path.parts
    ):
        raise ValueError("Indexed Markdown must remain within the selected bundle")
    root = manifest_path.resolve().parent
    descriptors = []
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        descriptors.append(directory)
        for part in path.parts[:-1]:
            directory = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            descriptors.append(directory)
        descriptor = os.open(
            path.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        descriptors.append(descriptor)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Indexed Markdown is not a regular file")
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            raw = stream.read(MAX_MODULE_BYTES + 1)
        if len(raw) > MAX_MODULE_BYTES:
            raise ValueError("Indexed Markdown exceeds module size bound")
        if hashlib.sha256(raw).hexdigest() != module.content_hash:
            raise ValueError("Indexed Markdown content hash does not match")
        return raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("Indexed Markdown is unavailable or unsafe") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def load_knowledge(manifest_path: Path) -> KnowledgeManifest:
    try:
        manifest = KnowledgeManifest.model_validate_json(
            Path(manifest_path).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError) as exc:
        raise ValueError("Knowledge manifest is unavailable") from exc
    if manifest.status == "approved" and (
        not manifest.owner_approval_ref or not manifest.owner_approval_ref.strip()
    ):
        raise ValueError("Approved knowledge requires meaningful owner approval")
    for module in manifest.modules:
        read_module(manifest_path, module)
    return manifest
