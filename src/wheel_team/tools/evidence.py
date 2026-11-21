"""Run-local reads and small typed publication tools; role factories select writers."""

import json
from typing import Any, Literal

from wheel_team.contracts import ExpertReview, IdeaRevision, QualityAssessment, ToolResult
from wheel_team.vault import (
    EvidenceStore,
    InvalidRecord,
    RecordKind,
    RecordNotFound,
    record_reference,
)

from .fundamentals import fundamentals_view

_INDEX_FIELDS = {
    "evidence": (
        "evidence_id",
        "source",
        "source_url",
        "fetched_at",
        "source_timestamp",
        "source_time_raw",
        "source_time_availability",
        "source_timezone",
        "timezone_state",
        "delay_state",
        "delay_seconds",
        "coverage",
        "content_hash",
    ),
    "calculation": ("calculation_id", "formula", "formula_version", "status", "computed_at"),
    "snapshot": ("snapshot_id", "account_alias", "source", "as_of", "fetched_at", "completeness"),
    "idea": ("idea_id", "revision_id", "company_id", "status", "author", "task_id", "created_at"),
    "review": ("review_id", "run_id", "round", "created_at"),
    "quality": ("assessment_id", "company_id", "assessed_at", "conclusion"),
}
_CONTENT_INDEX_FIELDS = ("capability", "instrument_id", "capture_id", "operation", "title")
_FIND_GUIDANCE = (
    'Use kind="evidence", "calculation", "snapshot", "idea", "review", or "quality". '
    'For source observations use evidence_find(kind="evidence"); capability names are not kinds. '
    'query matches exact top-level fields, e.g. {"source":"yahoo-underlying-quotes"}. '
    "Use integer limit 1..50 (default20) and offset >=0 to page the index."
)
_READ_GUIDANCE = (
    'Use the exact record_ref returned by evidence_find(kind="evidence") or another valid kind. '
    "Examples: evidence:quote-1, evidence:public-web:source-id, idea:idea-id@revision-id. "
    "Bare IDs, capability names and paths are not references; do not guess or add prefixes."
)


def _index_record(kind: RecordKind, record: dict[str, Any]) -> dict[str, Any]:
    """Select bounded identity/source metadata; full bodies remain behind exact reads."""
    metadata: dict[str, Any] = {}
    truncated: list[str] = []

    def bounded(path: str, value: Any) -> Any:
        maximum = 1024 if path == "source_url" else 256
        if isinstance(value, str) and len(value) > maximum:
            truncated.append(path)
            return value[:maximum]
        if isinstance(value, list):
            # coverage is the only list selected by this fixed projection.
            if len(value) > 8 or any(len(item) > 128 for item in value):
                truncated.append(path)
            return [item[:128] for item in value[:8]]
        return value

    for field in _INDEX_FIELDS[kind]:
        metadata[field] = bounded(field, record[field])
    omitted = [field for field in record if field not in metadata and field != "content"]
    content = record.get("content")
    if isinstance(content, dict):
        selected_content = {}
        for field in _CONTENT_INDEX_FIELDS:
            if field in content and isinstance(content[field], str):
                path = "content." + field
                selected_content[field] = bounded(path, content[field])
        if selected_content:
            metadata["content"] = selected_content
        omitted.extend("content." + field for field in content if field not in selected_content)
    elif "content" in record:
        omitted.append("content")
    return {
        "record_ref": record_reference(kind, record),
        "metadata": metadata,
        "omitted_fields": [path[:256] for path in omitted[:32]],
        "omitted_field_count": len(omitted),
        "omitted_fields_truncated": len(omitted) > 32 or any(len(path) > 256 for path in omitted),
        "truncated_fields": truncated,
    }


def bounded_record_view(record: Any) -> Any:
    """Keep small records exact; label bounded serialized excerpts of large originals."""
    serialized = json.dumps(record, ensure_ascii=True, separators=(",", ":"))
    if len(serialized) <= 12000:
        return record
    excerpt = serialized[:8000]
    return {
        "serialized_json_excerpt": excerpt,
        "tool_response_projection": {
            "view": "bounded_json_excerpt_v1",
            "excerpt_only": True,
            "selection": "first_8000_chars_of_ascii_json; may end mid-field",
            "original_serialized_chars": len(serialized),
            "omitted_chars": len(serialized) - len(excerpt),
            "stored_record_unchanged": True,
            "hash_semantics": "original_record_and_source_not_projection",
            "guidance": "Excerpt is incomplete; do not infer absent facts or complete claims. "
            "Use the original record_ref for fact_check and audit.",
        },
    }


def evidence_tools(store: EvidenceStore) -> dict[str, Any]:
    async def evidence_read(
        record_ref: str, view: Literal["compact"] = "compact"
    ) -> dict[str, Any]:
        """Read an immutable record using an exact record_ref from evidence_find.

        Examples: evidence:quote-1, evidence:public-web:source-id, idea:idea-id@revision-id.
        Bare evidence IDs are invalid. Discover references with evidence_find(kind="evidence")
        or the appropriate kind; do not guess a prefix or revision. view="compact" (default)
        selects fundamentals business/financial fields. Large records return a labeled
        bounded JSON excerpt, which may end mid-field. Omitted text is not evidence of absence.
        Originals remain available to audit and fact_check by the same reference.
        """
        if view != "compact":
            return ToolResult(
                status="invalid_data", reason='Use view="compact" for evidence_read'
            ).model_dump(mode="json")
        try:
            record = store.get(record_ref)
            return ToolResult(
                status="success",
                data={
                    "record_ref": record_ref,
                    "record": bounded_record_view(fundamentals_view(record, record_ref)),
                },
                result_refs=(record_ref,),
            ).model_dump(mode="json")
        except RecordNotFound:
            return ToolResult(
                status="unavailable", reason="Record is absent from this run. " + _READ_GUIDANCE
            ).model_dump(mode="json")
        except InvalidRecord:
            return ToolResult(
                status="invalid_data", reason="Invalid logical record reference. " + _READ_GUIDANCE
            ).model_dump(mode="json")

    async def evidence_find(
        kind: RecordKind, query: dict[str, Any] | None = None, limit: int = 20, offset: int = 0
    ) -> dict[str, Any]:
        """Discover a compact metadata index with canonical record_ref/result_refs, not bodies.

        Valid kinds: evidence, calculation, snapshot, idea, review, quality. Source observations
        use kind="evidence"; quote, market_observation, research_result, underlying_quotes and
        option_chains are NOT kinds. Query matches exact top-level fields, e.g.
        {"source":"yahoo-underlying-quotes"}. Start with evidence_find(kind="evidence").
        limit defaults20, maximum50; offset defaults0. Follow next_offset to access omitted rows.
        data.projection declares the metadata allowlist, field bounds and body_omitted once.
        Content metadata selects only allowlisted string fields actually present in the record.
        Each row retains dynamic omitted_fields/count and truncated_fields; no summary is invented.
        Use evidence_read(record_ref=<exact returned record_ref>) for bounded source views
        and provenance; large records are explicitly labeled excerpts.
        Example refs: evidence:quote-1; idea:idea-id@revision-id.
        Never use bare IDs.
        """
        try:
            if (
                type(limit) is not int
                or not 1 <= limit <= 50
                or type(offset) is not int
                or offset < 0
            ):
                raise InvalidRecord("Invalid index bounds")
            rows = store.find(kind, {} if query is None else query)
            index = [_index_record(kind, row) for row in rows[offset : offset + limit]]
            refs = tuple(row["record_ref"] for row in index)
            return ToolResult(
                status="success",
                data={
                    "records": index,
                    "projection": {
                        "kind": kind,
                        "top_level_fields": list(_INDEX_FIELDS[kind]),
                        "content_fields": list(_CONTENT_INDEX_FIELDS) if kind == "evidence" else [],
                        "content_selection": "allowlisted_string_fields_only_if_present",
                        "body_omitted": True,
                        "field_bounds": {
                            "string_chars": 256,
                            "source_url_chars": 1024,
                            "coverage_items": 8,
                            "coverage_item_chars": 128,
                            "omitted_field_paths": 32,
                            "omitted_field_path_chars": 256,
                        },
                        "bounded_record_tool": "evidence_read",
                    },
                    "total": len(rows),
                    "returned": len(index),
                    "limit": limit,
                    "offset": offset,
                    "next_offset": offset + len(index) if offset + len(index) < len(rows) else None,
                    "selection": "exact_top_level_query_then_store_order_offset_limit",
                },
                result_refs=refs,
            ).model_dump(mode="json")
        except InvalidRecord:
            return ToolResult(
                status="invalid_data", reason="Invalid record query. " + _FIND_GUIDANCE
            ).model_dump(mode="json")

    def publish(kind: str, record: dict[str, Any]) -> dict[str, Any]:
        try:
            # Only declared reference fields are traversed. Provider prose/raw JSON are data.
            for field, expected in (
                ("evidence_refs", "evidence"),
                ("calculation_refs", "calculation"),
                ("reviewed_revision_refs", "idea"),
            ):
                for ref in record.get(field, ()):
                    if not isinstance(ref, str) or not ref.startswith(expected + ":"):
                        raise InvalidRecord("Incorrect reference kind")
                    store.get(ref)
            for field, expected in (
                ("snapshot_ref", "snapshot"),
                ("quality_ref", "quality"),
                ("parent_revision", "idea"),
            ):
                ref = record.get(field)
                if ref is not None:
                    if not isinstance(ref, str) or not ref.startswith(expected + ":"):
                        raise InvalidRecord("Incorrect reference kind")
                    parent = store.get(ref)
                    if field == "parent_revision" and (
                        parent["idea_id"] != record.get("idea_id")
                        or parent["company_id"] != record.get("company_id")
                    ):
                        raise InvalidRecord("Parent revision belongs to another idea")
                    if field == "quality_ref" and parent["company_id"] != record.get("company_id"):
                        raise InvalidRecord("Quality assessment belongs to another company")
            ref = store.put(kind, record)
            return ToolResult(
                status="success", data={"record_ref": ref}, result_refs=(ref,)
            ).model_dump(mode="json")
        except (InvalidRecord, RecordNotFound, TypeError, KeyError):
            return ToolResult(
                status="invalid_data",
                reason="Publication is invalid, unresolved or conflicts with an immutable record",
            ).model_dump(mode="json")

    async def publish_idea(idea: IdeaRevision) -> dict[str, Any]:
        """Publish a typed idea revision with existing evidence/calculation references."""
        return publish(
            "idea", idea.model_dump(mode="json") if isinstance(idea, IdeaRevision) else idea
        )

    async def publish_review(review: ExpertReview) -> dict[str, Any]:
        """Publish a typed expert review of existing immutable idea revisions."""
        return publish(
            "review", review.model_dump(mode="json") if isinstance(review, ExpertReview) else review
        )

    async def publish_quality(assessment: QualityAssessment) -> dict[str, Any]:
        """Publish a dated quality assessment backed by run-local evidence references."""
        return publish(
            "quality",
            assessment.model_dump(mode="json")
            if isinstance(assessment, QualityAssessment)
            else assessment,
        )

    return {
        "evidence_read": evidence_read,
        "evidence_find": evidence_find,
        "publish_idea": publish_idea,
        "publish_review": publish_review,
        "publish_quality": publish_quality,
    }
