"""Selected exact owner feedback, searchable Markdown and explicit maintenance receipts."""

import base64
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from wheel_team.config import TeamConfig
from wheel_team.continuity import ContinuityFiles
from wheel_team.followup import load_parent_review
from wheel_team.types import TeamDependencies, TeamRequest


class FeedbackSources:
    """No content classifier: only the caller selects which original to preserve."""

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        media_roots: list[Path] | None = None,
    ):
        self.files = ContinuityFiles(root)
        self.clock = clock
        self.media_roots = [p.resolve() for p in media_roots or []]

    def media(self, original: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        saved, errors = [], []
        values = [("file", value) for value in dict.fromkeys(original.get("attachment_paths", []))]
        values += [("inline", value) for value in dict.fromkeys(original.get("inline_images", []))]
        for kind, value in values:
            try:
                if kind == "file":
                    source = Path(value).expanduser()
                    if source.is_symlink() or not any(
                        source.resolve().is_relative_to(r) for r in self.media_roots
                    ):
                        raise ValueError("Attachment outside configured media cache roots")
                    from wheel_team.followup import read_source

                    raw = read_source(source, 32 * 1024 * 1024)
                    suffix = source.suffix.lower()
                else:
                    prefix, encoded = value.split(",", 1)
                    types = {
                        "data:image/jpeg;base64": ".jpg",
                        "data:image/png;base64": ".png",
                        "data:image/webp;base64": ".webp",
                        "data:image/gif;base64": ".gif",
                    }
                    if prefix not in types or len(encoded) > 45 * 1024 * 1024:
                        raise ValueError("Unsupported inline image representation")
                    raw = base64.b64decode(encoded, validate=True)
                    suffix = types[prefix]
                if suffix not in {
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp",
                    ".gif",
                    ".ogg",
                    ".opus",
                    ".mp3",
                    ".wav",
                    ".m4a",
                }:
                    raise ValueError("Unsupported media extension")
                digest = hashlib.sha256(raw).hexdigest()
                ref = "attachments/feedback/" + digest + suffix
                target = self.files._control("attachments", "feedback/" + digest + suffix)
                if target.exists():
                    if target.read_bytes() != raw:
                        raise ValueError("Immutable media differs")
                else:
                    self.files._atomic(target, raw)
                saved.append({"path": ref, "sha256": digest, "bytes": len(raw), "origin": kind})
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
        return saved, errors

    def _control(self, key: str) -> Path:
        return self.files._control(".feedback", key + ".json")

    def capture(self, original: dict[str, Any]) -> dict[str, Any]:
        text = original["text"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Feedback requires exact nonempty original text")
        raw = text.encode("utf-8")
        digest = hashlib.sha256(
            str(original.get("transport_key") or uuid4().hex).encode()
        ).hexdigest()
        control = self._control(digest)
        with self.files._lock():
            attachments, attachment_errors = self.media(original)
            later_input = ""
            if control.exists():
                record = json.loads(control.read_text())
                if record["body_sha256"] != hashlib.sha256(raw).hexdigest():
                    later_input = raw.decode("utf-8")
                    raw = self.files._raw(record["source_ref"])[record["body_offset"] :]
            else:
                now = self.clock()
                if now.utcoffset() is None:
                    raise ValueError("Recording clock must be timezone-aware")
                now = now.astimezone(UTC)
                recorded = now.isoformat()
                ref = (
                    "sources/feedback/"
                    + now.strftime("%Y-%m-%d/%H-%M-%S-%f")
                    + "-owner-"
                    + digest[:12]
                    + ".md"
                )
                header = (
                    "# Selected owner feedback source\n\nRecorded at: " + recorded + "\n"
                    "Source: " + str(original.get("platform", "owner supplied text")) + "\n"
                    "Message timestamp: " + str(original.get("message_at") or "not supplied") + "\n"
                    "Original caption when separately associated: "
                    + json.dumps(original.get("raw_caption"), ensure_ascii=False)
                    + "\n"
                    "Attribution: "
                    + str(original.get("input_attribution", "Original owner statement"))
                    + "\n"
                    "Media originals: " + json.dumps(attachments, ensure_ascii=False) + "\n"
                    "Media limitations: " + json.dumps(attachment_errors, ensure_ascii=False) + "\n"
                    "No inference of execution beyond the supplied source claims.\n\n"
                    "## Selected current input\n\n"
                ).encode()
                record = {
                    "key": digest,
                    "source_ref": ref,
                    "recorded_at": recorded,
                    "message_at": original.get("message_at"),
                    "body_offset": len(header),
                    "body_sha256": hashlib.sha256(raw).hexdigest(),
                    "source_sha256": hashlib.sha256(header + raw).hexdigest(),
                    "header": header.decode(),
                    "provenance": original.get("provenance", {}),
                    "input_attribution": original.get(
                        "input_attribution", "Original owner statement"
                    ),
                    "attachments": attachments,
                    "attachment_errors": attachment_errors,
                }
                derived = original.get("transformed_input") or ""
                transcription = original.get("transcription") or ""
                if derived or transcription:
                    record["derived_source_ref"] = ref.removesuffix(".md") + "-transcription.md"
                    record["derived_text"] = (
                        "# Derived Hermes input / transcription\n\nNot verbatim owner text.\n"
                        "Original caption/source: "
                        + ref
                        + "\nRecorded at: "
                        + recorded
                        + "\nOriginal media: "
                        + json.dumps(attachments, ensure_ascii=False)
                        + "\nMedia limitations: "
                        + json.dumps(attachment_errors)
                        + "\n\n## Existing Hermes enrichment (may contain recognition errors)\n\n"
                        + derived
                        + "\n\n## Additional attributed transcription / visual reading\n\n"
                        + transcription
                    )
                # Reserve the same pathname before writing so a repeated platform update
                # recovers an interrupted capture without creating another source.
                self.files._atomic(
                    control, (json.dumps(record, ensure_ascii=False) + "\n").encode()
                )
            target = self.files._path(record["source_ref"])
            whole = record["header"].encode() + raw
            if target.exists():
                if target.read_bytes() != whole:
                    raise ValueError("Immutable feedback original changed")
            else:
                self.files._atomic(target, whole)
            if record.get("derived_source_ref"):
                derived_path = self.files._path(record["derived_source_ref"])
                content = record["derived_text"].encode()
                if not derived_path.exists():
                    self.files._atomic(derived_path, content)
                elif derived_path.read_bytes() != content:
                    raise ValueError("Immutable transcript differs")
            supplement_refs = []
            supplement = {
                "later_selected_input": later_input,
                "input_attribution": original.get("input_attribution"),
                "transcription": original.get("transcription", ""),
                "attachments": attachments,
                "attachment_errors": attachment_errors,
            }
            if later_input or supplement["transcription"] or attachments or attachment_errors:
                body = json.dumps(supplement, ensure_ascii=False, indent=2)
                ref = (
                    record["source_ref"].removesuffix(".md")
                    + "-supplement-"
                    + hashlib.sha256(body.encode()).hexdigest()
                    + ".md"
                )
                dest = self.files._path(ref)
                if not dest.exists():
                    self.files._atomic(
                        dest,
                        (
                            "# Attributed selected input / transcription supplement\n\n"
                            "Original source: "
                            + record["source_ref"]
                            + "\nCaptured at: "
                            + self.clock().astimezone(UTC).isoformat()
                            + "\nAdditional reading is not verbatim owner text "
                            "unless explicitly attributed as such.\n\n" + body
                        ).encode(),
                    )
                supplement_refs.append(ref)
            record = {
                **record,
                "supplement_sources": supplement_refs,
                "attachments": attachments or record.get("attachments", []),
                "attachment_errors": attachment_errors,
            }
        return {
            key: value
            for key, value in record.items()
            if key not in {"header", "provenance", "derived_text"}
        }

    def load(self, source_ref: str) -> dict[str, Any]:
        self.files._path(source_ref)
        for p in self.files._control(".feedback", "").glob("*.json"):
            record = json.loads(p.read_text())
            if record.get("source_ref") == source_ref:
                raw = self.files._raw(source_ref)
                if hashlib.sha256(raw).hexdigest() != record["source_sha256"]:
                    raise ValueError("Feedback original hash changed")
                return cast(dict[str, Any], record)
        raise ValueError("Selected feedback source is not an original recording")

    def original(self, source_ref: str) -> str:
        record = self.load(source_ref)
        return self.files._raw(source_ref)[record["body_offset"] :].decode("utf-8")

    def context(self, refs: list[str]) -> str:
        texts = []
        for ref in dict.fromkeys(refs):
            if not ref.endswith(".md") or not ref.startswith(("sources/feedback/", "vault/")):
                raise ValueError("Context must select scoped feedback Markdown or a vault note")
            raw = self.files._raw(ref)
            with self.files._lock():
                snapshot = self.files._snapshot(ref, raw)
            attribution = (
                "Continuity-authored derived note; not an original owner or Expert statement"
                if ref.startswith("vault/")
                else "Recorded original feedback or attributed owner/team discussion"
            )
            texts.append(
                "Selected context path: "
                + ref
                + "\nAttribution: "
                + attribution
                + "\nSnapshot: "
                + snapshot
                + "\nRevision: "
                + hashlib.sha256(raw).hexdigest()
                + "\nCaptured at: "
                + self.clock().astimezone(UTC).isoformat()
                + "\nExisting dates and attribution in the selected text remain unchanged.\n\n"
                + raw.decode("utf-8")
            )
        return "\n\n".join(texts)

    def previous(self, record: dict[str, Any]) -> dict[str, Any] | None:
        path = self.files._control(".feedback-work", record["key"] + ".json")
        with self.files._lock():
            return cast(dict[str, Any], json.loads(path.read_text())) if path.exists() else None

    def reserve(self, record: dict[str, Any]) -> dict[str, Any] | None:
        path = self.files._control(".feedback-work", record["key"] + ".json")
        with self.files._lock():
            if path.exists():
                return cast(dict[str, Any], json.loads(path.read_text()))
            self.files._atomic(
                path,
                b'{"status":"reserved","duplicate":true,'
                b'"error":"Prior invocation reserved; no automatic rerun"}\n',
            )
        return None

    def finish(self, record: dict[str, Any], outcome: dict[str, Any]) -> None:
        path = self.files._control(".feedback-work", record["key"] + ".json")
        with self.files._lock():
            self.files._atomic(path, (json.dumps(outcome, ensure_ascii=False) + "\n").encode())

    def discussion(
        self,
        record: dict[str, Any],
        current: dict[str, Any],
        parent: str | None,
        result: Any,
        maintenance: dict[str, Any],
    ) -> str:
        ref = str(current["source_ref"]).removesuffix(".md") + "-discussion.md"
        experts = json.loads(Path(result.artifacts["expert"]).read_text())
        manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
        council = manifest.get("final_author") == "portfolio_manager"
        record_only = manifest.get("feedback_record_only", False)
        expert_name = "Portfolio Manager" if council else "Expert"
        text = (
            "# Owner / portfolio-team discussion\n\nRecorded at: "
            + self.clock().isoformat()
            + "\nOriginal owner source: "
            + record["source_ref"]
            + "\nCurrent selected message source: "
            + current["source_ref"]
            + "\nOriginal parent report: "
            + str(parent or "unresolved/not selected")
            + "\nDated parent observations retain their original dates.\n\n"
            + "## Selected feedback input\n\nAttribution: "
            + str(record.get("input_attribution", "Original owner statement"))
            + "\n\n"
            + self.original(record["source_ref"])
        )
        if current["source_ref"] != record["source_ref"]:
            text += (
                "\n\n## Selected current clarification (see source attribution)\n\n"
                + self.original(current["source_ref"])
            )
        for n, expert in enumerate(experts, 1):
            heading = (
                "Saved incomplete " + expert_name + " invocation envelope "
                if expert.get("error")
                else "Original " + expert_name + " response "
            )
            text += "\n\n## " + heading + str(n) + "\n\n" + expert["assessment"]
            if expert.get("error"):
                text += "\n\nExpert execution limitation: " + expert["error"]
        lead_summary = result.summary
        if council:
            specialists = json.loads(Path(result.artifacts["specialists"]).read_text())
            for specialist in specialists:
                text += (
                    "\n\n## Supporting specialist original: "
                    + specialist["role"]
                    + " ("
                    + specialist["outcome"]
                    + ")\n\n"
                    + json.dumps(specialist, ensure_ascii=False, indent=2)
                )
            lead_summary = json.loads(Path(result.artifacts["lead_coordination"]).read_text())[
                "summary"
            ]
        if record_only:
            text += (
                "\n\n## Recording-only acknowledgment (no investment assessment)\n\n"
                + result.summary
            )
        else:
            text += (
                "\n\n## Lead coordination response (not an investment assessment)\n\n"
                + lead_summary
            )
        text += (
            "\n\n## Actual maintenance receipts\n\n"
            + json.dumps(maintenance, ensure_ascii=False, indent=2)
            + "\n"
        )
        with self.files._lock():
            dest = self.files._path(ref)
            if dest.exists() and dest.read_bytes() != text.encode():
                raise ValueError("Discussion original already exists with different bytes")
            if not dest.exists():
                self.files._atomic(dest, text.encode())
        return ref


async def run_feedback(
    payload: dict[str, Any], *, config: TeamConfig, deps: TeamDependencies | None = None
) -> dict[str, Any]:
    """Preserve selected exact owner text first; no new work on repeated updates."""
    if config.continuity is None:
        raise ValueError("Feedback requires the configured durable continuity root")
    sources = FeedbackSources(config.continuity.root, media_roots=config.feedback_media_roots)
    captured = sources.capture(payload)
    try:
        record = sources.load(payload.get("prior_source") or captured["source_ref"])
    except (OSError, ValueError) as exc:
        return {
            "source_preserved": True,
            "source_ref": captured["source_ref"],
            "current_source_ref": captured["source_ref"],
            "status": "needs_context",
            "notes_written": False,
            "error": str(exc),
            "response": (
                "The current original is saved; identify the earlier feedback source to continue."
            ),
        }
    outcome: dict[str, Any] = {
        "source_preserved": True,
        "source_ref": record["source_ref"],
        "current_source_ref": captured["source_ref"],
        "recorded_at": record["recorded_at"],
        "attachments": captured.get("attachments", []),
        "attachment_errors": captured.get("attachment_errors", []),
        "supplement_sources": captured.get("supplement_sources", []),
        "notes_written": False,
        "maintenance": None,
        "response": "",
        "error": None,
    }
    action = payload.get("action", "discuss")
    if action not in {"discuss", "record"}:
        return {**outcome, "status": "error", "error": "Unknown feedback action"}
    parent = payload.get("parent_review")
    if action == "discuss":
        try:
            if not parent:
                raise ValueError("Select the intended dated report or clarify its context")
            load_parent_review(Path(parent), report_only=True)
        except (OSError, ValueError) as exc:
            return {
                **outcome,
                "status": "needs_context",
                "error": str(exc),
                "response": (
                    "Your original feedback is saved. Please identify the intended "
                    "report/date; no report was guessed."
                ),
            }
    prior = sources.previous(captured)
    if prior is not None:
        return {
            **outcome,
            **prior,
            "duplicate": True,
            "supplement_sources": captured.get("supplement_sources", []),
            "attachment_errors": captured.get("attachment_errors", []),
            "attachments": captured.get("attachments", []),
            "additional_case_update": False,
        }
    try:
        context_refs = list(payload.get("context_paths") or [])
        context_refs.extend(captured.get("supplement_sources", []))
        for selected in [record, captured]:
            if selected.get("derived_source_ref"):
                context_refs.append(selected["derived_source_ref"])
        if record["source_ref"] != captured["source_ref"]:
            context_refs.append(captured["source_ref"])
        context = sources.context(context_refs) or None
    except (OSError, ValueError) as exc:
        return {
            **outcome,
            "status": "needs_context",
            "error": "Context validation: " + str(exc),
            "response": (
                "Original feedback is preserved; correct the selected context before discussion."
            ),
        }
    prior = sources.reserve(captured)
    if prior is not None:
        return {
            **outcome,
            **prior,
            "duplicate": True,
            "supplement_sources": captured.get("supplement_sources", []),
            "attachment_errors": captured.get("attachment_errors", []),
            "attachments": captured.get("attachments", []),
            "additional_case_update": False,
        }
    try:
        request = TeamRequest(
            question=sources.original(record["source_ref"]),
            mode="portfolio",
            parent_review=parent,
            review_kind="targeted" if parent else "full",
            feedback_source_ref=record["source_ref"],
            feedback_source_attribution=record.get("input_attribution"),
            feedback_context=context,
            feedback_record_only=action == "record",
        )
        if deps is None:
            from wheel_team.adapters.market.captured import CapturedMarketProvider
            from wheel_team.adapters.market.spark import SparkMarketProvider
            from wheel_team.adapters.research import HermesResearchBackend

            deps = TeamDependencies(
                evaluation_mode="team" if config.model.provider == "openai" else "scripted",
                market_provider=SparkMarketProvider(config.market_backend)
                if config.market_backend
                else CapturedMarketProvider(config.market_capture_manifest_path)
                if config.market_capture_manifest_path
                else None,
                research_backend=HermesResearchBackend(config.research_backend)
                if config.research_backend
                else None,
            )
        from wheel_team.team import run_team

        result = await run_team(request, config=config, deps=deps)
        maintenance = json.loads(Path(result.artifacts["feedback_maintenance"]).read_text())
        outcome.update(
            status="completed"
            if result.completion_reason == "complete" and maintenance.get("completed")
            else "partial",
            discussion_completion_reason=result.completion_reason,
            response=result.summary,
            run_id=result.run_id,
            result_path=result.artifacts["result"],
            maintenance=maintenance,
            notes_written=bool(maintenance.get("writes")),
        )
        outcome["durable_note_status"] = (
            "failed"
            if not maintenance.get("completed")
            else "updated"
            if maintenance.get("writes")
            else "no_changes"
        )
        if not maintenance.get("completed"):
            outcome["response"] += (
                "\n\nOperational note: Durable note maintenance did not complete. "
                "The original feedback is preserved, but future reviews cannot assume "
                "these preferences or corrections were incorporated into durable notes."
            )
        # Checkpoint confirmed native and maintenance results BEFORE archival.
        # An archive failure cannot erase actual writes or authorize another run.
        sources.finish(captured, outcome)
        try:
            outcome["discussion_ref"] = sources.discussion(
                record, captured, parent, result, maintenance
            )
        except Exception as exc:
            outcome.update(status="partial", archive_error=type(exc).__name__)
    except Exception as exc:
        outcome.update(
            status="error",
            error=type(exc).__name__,
            error_detail=str(exc) if isinstance(exc, ValueError) else None,
            response="Original feedback is preserved; discussion/maintenance did not finish.",
        )
    sources.finish(captured, outcome)
    return outcome
