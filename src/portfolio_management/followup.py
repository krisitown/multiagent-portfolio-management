"""Parent-linked ordinary evidence reuse; no conversation or hidden-reasoning reads."""

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any, cast

from portfolio_management.types import ExpertResult, ResearchResult, TeamRequest, TeamResult
from portfolio_management.vault import MAX_RECORD_BYTES, RecordKind, record_reference

ORDINARY_FILES = (
    "request.json",
    "result.json",
    "report.md",
    "manifest.json",
    "research-results.json",
    "expert-results.json",
    "specialist-assessments.json",
    "lead-coordination.json",
    "asymmetric-results.json",
    "explorer-results.json",
    "expert-research-requests.json",
    "parent-review.json",
)
REQUIRED_FILES = frozenset({"request.json", "result.json", "report.md", "manifest.json"})
STORE_NAMES = ("records", "public-records", "private-records")
MAX_ORDINARY_BYTES = 8 * 1024 * 1024


def prompt_review(packet: dict[str, Any]) -> dict[str, Any]:
    """Keep decision originals inline without repeating per-record audit inventories.

    Project only at model boundaries: the saved packet and imported stores retain
    every ledger. Exclude known top-level inventories rather than whitelisting
    decision fields, so new metadata and report-local source dates survive.
    """
    projected = dict(packet)
    omitted = {
        key: len(projected.pop(key))
        for key in ("artifact_hashes", "record_hashes", "source_dates")
        if key in projected
    }
    if omitted:
        projected["audit_omissions"] = {
            "entry_counts": omitted,
            "scope": (
                "Only audit inventories are omitted from this model prompt. The full "
                "packet remains saved in parent-review.json; original reports and their "
                "attribution above are unchanged. Imported records retain original source "
                "dates and contents. Investment experts and public researchers can use "
                "evidence_find and evidence_read within their existing private/public "
                "access scope; inheritance does not refresh observations."
            ),
        }
    return projected


def read_source(path: Path, limit: int = MAX_ORDINARY_BYTES) -> bytes:
    """Bounded regular-file read without following a selected file symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("Parent artifact must be a regular file")
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Parent artifact exceeds size bound")
    return raw


def ordinary_paths(root: Path) -> list[Path]:
    paths = [root / name for name in ORDINARY_FILES if (root / name).exists()]
    for name in STORE_NAMES:
        directory = root / name
        if directory.is_symlink():
            raise ValueError("Parent record directory cannot be a symlink")
        if directory.is_dir():
            paths.extend(sorted(directory.glob("*.json")))
    return paths


def checkpoint_files(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(read_source(p)).hexdigest()
        for p in ordinary_paths(root)
    }


def load_parent_review(path: Path, *, report_only: bool = False) -> dict[str, Any]:
    """Validate a native run and return dated ordinary state plus scoped record imports."""
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Parent review must be an existing native run directory")
    root = path.resolve()
    hashes = checkpoint_files(root)
    if not REQUIRED_FILES <= hashes.keys():
        raise ValueError("Parent review is missing required ordinary artifacts")
    checkpoint = root / "review-checkpoint.json"
    if checkpoint.exists():
        seal = json.loads(read_source(checkpoint))
        if (
            not isinstance(seal, dict)
            or seal.get("run_id") != root.name
            or seal.get("files") != hashes
        ):
            raise ValueError("Parent artifact hashes no longer match review checkpoint")
    request = TeamRequest.model_validate_json(read_source(root / "request.json"))
    result = TeamResult.model_validate_json(read_source(root / "result.json"))
    report = read_source(root / "report.md").decode("utf-8")
    manifest = json.loads(read_source(root / "manifest.json"))
    if not isinstance(manifest, dict):
        raise ValueError("Parent manifest must be an object")
    if (result.lineage or "review_checkpoint" in result.artifacts) and not checkpoint.exists():
        raise ValueError("Parent review checkpoint is missing")
    if result.run_id != root.name or manifest.get("run_id") != result.run_id:
        raise ValueError("Parent review identities disagree")
    if result.report_markdown != report:
        raise ValueError("Parent original report disagrees with native result")
    if result.completed_at is None or manifest.get("completed_at") is None:
        raise ValueError("Parent review has not finalized")

    if report_only:
        # Owner feedback selects the delivered assessment, not every supporting
        # envelope or the old account inputs. Keep integrity checks above, but
        # do not import the parent's evidence stores into the new invocation.
        return {
            "packet": {
                "context_scope": "report_only",
                "parent_run_id": result.run_id,
                "review_session_id": result.lineage.get("review_session_id", result.run_id),
                "prior_completed_at": result.completed_at.isoformat(),
                "prior_completion_reason": result.completion_reason,
                "prior_report": report,
                "final_author": manifest.get("final_author", "team_lead"),
                "report_sha256": hashes["report.md"],
                "attribution": (
                    "Original dated assessment; not a current portfolio or verified new evidence."
                ),
                "freshness": (
                    "Re-research material claims when needed; historical evidence was not imported."
                ),
            },
            "request": request,
            "public_records": [],
            "private_records": [],
        }

    stores = manifest.get("record_stores", {})
    public_root = None
    if isinstance(stores, dict) and stores.get("public"):
        candidate = Path(stores["public"])
        if not candidate.is_absolute():
            candidate = root / candidate
        if (
            candidate.is_symlink()
            or candidate.resolve().parent != root
            or candidate.name not in STORE_NAMES
        ):
            raise ValueError("Parent public store classification escapes its run")
        if stores.get("private") and Path(stores["private"]).resolve() == candidate.resolve():
            raise ValueError("Parent public and private stores cannot coincide")
        public_root = candidate.resolve()

    public_records, private_records, source_dates = [], [], []
    record_hashes = {}
    for file in ordinary_paths(root):
        if file.parent == root:
            continue
        raw = read_source(file, MAX_RECORD_BYTES)
        envelope = json.loads(raw)
        if not isinstance(envelope, dict) or set(envelope) != {"kind", "ref", "record"}:
            raise ValueError("Invalid parent typed record envelope")
        kind, ref, record = envelope["kind"], envelope["ref"], envelope["record"]
        if record_reference(cast(RecordKind, kind), record) != ref:
            raise ValueError("Parent record identity mismatch")
        if file.name != hashlib.sha256(ref.encode()).hexdigest() + ".json":
            raise ValueError("Parent record filename does not match immutable reference")
        # Legacy stores have no capability provenance and remain private. Snapshot
        # history never becomes public merely because a path claims it is public.
        public = file.parent == public_root and kind != "snapshot"
        (public_records if public else private_records).append(envelope)
        source_dates.append(
            {
                "record_ref": ref,
                "scope": "public" if public else "private",
                **{
                    key: record[key]
                    for key in [
                        "source",
                        "source_timestamp",
                        "fetched_at",
                        "as_of",
                        "computed_at",
                        "content_hash",
                    ]
                    if key in record
                },
            }
        )
        record_hashes[ref] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "scope": "public" if public else "private",
        }

    def ordinary_json(name: str, expert: bool = False) -> Any:
        rows = json.loads(read_source(root / name)) if (root / name).exists() else []
        if not isinstance(rows, list):
            raise ValueError("Parent original envelopes must be a list")
        for row in rows:
            (ExpertResult if expert else ResearchResult).model_validate(row)
        return rows

    lineage = result.lineage
    packet = {
        "parent_run_id": result.run_id,
        "review_session_id": lineage.get("review_session_id", result.run_id),
        "prior_question": request.question,
        "prior_completed_at": result.completed_at.isoformat() if result.completed_at else None,
        "prior_completion_reason": result.completion_reason,
        "prior_report": report,
        "final_author": manifest.get("final_author", "team_lead"),
        "analyst_reports": ordinary_json("research-results.json"),
        "expert_reports": ordinary_json("expert-results.json", expert=True),
        "specialist_assessments": json.loads(read_source(root / "specialist-assessments.json"))
        if (root / "specialist-assessments.json").exists()
        else [],
        "asymmetric_reports": ordinary_json("asymmetric-results.json"),
        "explorer_reports": ordinary_json("explorer-results.json"),
        "artifact_hashes": hashes,
        "record_hashes": record_hashes,
        "source_dates": source_dates,
        "portfolio_revision": {
            "snapshot_id": request.portfolio.snapshot_id if request.portfolio else None,
            "as_of": request.portfolio.as_of.isoformat() if request.portfolio else None,
            "readable_text_sha256": hashlib.sha256(request.portfolio_text.encode()).hexdigest()
            if request.portfolio_text is not None
            else None,
            "source_request_sha256": hashes["request.json"],
        },
        "integrity_scope": "checkpoint_verified"
        if checkpoint.exists()
        else "legacy_native_consistency_verified",
        "freshness": "Prior observations retain original dates; reuse does not make them current.",
    }
    return {
        "packet": packet,
        "request": request,
        "public_records": public_records,
        "private_records": private_records,
    }


def inherit_private_inputs(request: TeamRequest, parent: TeamRequest) -> TeamRequest:
    updates: dict[str, Any] = {}
    if request.owner_context is None:
        updates["owner_context"] = parent.owner_context
    if request.portfolio is None and request.portfolio_text is None:
        updates.update(portfolio=parent.portfolio, portfolio_text=parent.portfolio_text)
    return request.model_copy(update=updates)
