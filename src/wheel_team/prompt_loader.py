"""Read approved hash-bound Markdown or explicitly selected development fixtures."""

import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from wheel_team.agents.base import CHECKED_AUTHORS
from wheel_team.config import PromptSet
from wheel_team.contracts import AgentRole, PromptBundle


class PromptReadinessError(ValueError):
    """The selected prompt bundle cannot be used in the requested mode."""


def load_caller_amendments(path: Path | None, prompts: dict) -> tuple[dict, dict]:
    """Freeze separately hashed host amendments without editing approved role bodies."""
    if path is None:
        return {}, {}
    path = path.resolve()
    manifest = json.loads(path.read_text())

    def read(relative: str, want: str) -> str:
        source = (path.parent / relative).resolve()
        if not source.is_relative_to(path.parent) or source.suffix != ".md":
            raise PromptReadinessError("Caller amendment path leaves selected bundle")
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != want:
            raise PromptReadinessError("Caller amendment hash changed")
        return raw.decode("utf-8")

    common = read(manifest["common"], manifest["common_sha256"])
    bodies, hashes = {}, {"common": manifest["common_sha256"]}
    for role, prompt in prompts.items():
        if role not in CHECKED_AUTHORS:
            continue
        selected = manifest["roles"][role]
        if selected["baseline_sha256"] != prompt.content_hash:
            raise PromptReadinessError("Caller amendment baseline differs from role prompt")
        bodies[role] = (
            common + "\n\n" + read(selected["amendment_path"], selected["amendment_sha256"])
        )
        hashes[role] = selected["amendment_sha256"]
    return bodies, hashes


class _Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    content_hash: str


class _CheckerPromptBundle(PromptBundle):
    """Internal checker identity; public author/delegation roles stay unchanged."""

    role: Literal["fact_checker"] = "fact_checker"


class _ContinuityPromptBundle(PromptBundle):
    """Private memory role; not a public delegation role."""

    role: Literal["continuity"] = "continuity"  # type: ignore[assignment]


class _CouncilPromptBundle(PromptBundle):
    """Private investment council identity; public researcher inventory stays scoped."""

    role: Literal["dalio", "buffett", "portfolio_manager"]  # type: ignore[assignment]


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt_set_id: str
    version: str
    status: Literal["development", "approved"]
    owner_approval_ref: str | None = None
    roles: dict[str, _Source]
    shared: dict[str, _Source] = {}
    modules: dict[str, _Source] = {}


def load_prompt(
    role: AgentRole
    | Literal["fact_checker", "continuity", "dalio", "buffett", "portfolio_manager"],
    prompt_set: PromptSet,
) -> PromptBundle:
    manifest_path = prompt_set.manifest_path
    if manifest_path is None:
        if prompt_set.mode != "development":
            raise PromptReadinessError("Production prompt manifest is required")
        filename = "continuity-manifest.json" if role == "continuity" else "manifest.json"
        manifest_path = Path(str(files("wheel_team").joinpath("prompts/development", filename)))
    manifest_path = manifest_path.resolve()
    try:
        manifest = _Manifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as exc:
        raise PromptReadinessError("Prompt manifest is missing or invalid") from exc
    if manifest.status == "development" and prompt_set.mode != "development":
        raise PromptReadinessError("Development prompts require explicit development selection")
    if manifest.status == "approved" and (
        not manifest.owner_approval_ref or not manifest.owner_approval_ref.strip()
    ):
        raise PromptReadinessError("Approved prompts require an owner approval reference")
    if role not in manifest.roles:
        raise PromptReadinessError(f"Prompt manifest has no role: {role}")

    def read(source: _Source) -> str:
        path = (manifest_path.parent / source.path).resolve()
        # Shared modules may be inside the installed package's knowledge directory.
        allowed = manifest_path.parent
        if prompt_set.manifest_path is None:
            allowed = Path(str(files("wheel_team"))).resolve()
        if not path.is_relative_to(allowed) or path.suffix != ".md":
            raise PromptReadinessError("Prompt manifest Markdown path leaves the selected bundle")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise PromptReadinessError("Prompt Markdown is missing") from exc
        if hashlib.sha256(raw).hexdigest() != source.content_hash:
            raise PromptReadinessError("Prompt approval hash does not match Markdown content")
        try:
            return raw.decode("utf-8")
        except UnicodeError as exc:
            raise PromptReadinessError("Prompt Markdown is not UTF-8") from exc

    role_source = manifest.roles[role]
    content = [read(role_source)]
    content.extend(read(source) for source in manifest.shared.values())
    content.extend(read(source) for source in manifest.modules.values())
    bundle_type = (
        _CheckerPromptBundle
        if role == "fact_checker"
        else _ContinuityPromptBundle
        if role == "continuity"
        else _CouncilPromptBundle
        if role in {"dalio", "buffett", "portfolio_manager"}
        else PromptBundle
    )
    return bundle_type(
        prompt_id=f"{manifest.prompt_set_id}:{role}",
        role=role,
        version=manifest.version,
        content="\n\n".join(content),
        content_hash=role_source.content_hash,
        status=manifest.status,
        owner_approval_ref=manifest.owner_approval_ref,
        shared_hashes={name: source.content_hash for name, source in manifest.shared.items()},
        module_hashes={name: source.content_hash for name, source in manifest.modules.items()},
        source_path=str(manifest_path.parent / role_source.path),
    )
