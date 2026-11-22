"""Two direct analyst public-research tools; fetched pages remain untrusted evidence."""

import hashlib
import ipaddress
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from portfolio_management.adapters.research import HermesResearchBackend, ResearchError
from portfolio_management.contracts import EvidenceRecord, ToolResult
from portfolio_management.vault import EvidenceStore, InvalidRecord


def public_url(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(c.isspace() or ord(c) < 32 for c in value)
    ):
        raise ValueError("Expected a bounded public URL")
    url = urlsplit(value)
    host = (url.hostname or "").lower().rstrip(".")
    if (
        url.scheme not in {"http", "https"}
        or url.username is not None
        or url.password is not None
        or not host
        or url.port not in {None, 80, 443}
        or host.endswith((".local", ".localhost", ".internal"))
    ):
        raise ValueError("Expected a public HTTP(S) URL without credentials")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if (
            "." not in host
            or not re.fullmatch(r"[a-z0-9.-]+", host)
            or all(re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", part) for part in host.split("."))
        ):
            raise ValueError("Expected a public hostname") from None
    else:
        if not address.is_global:
            raise ValueError("Private network URLs are unavailable")
    return value


def _source_time(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("source_time_raw") or row.get("source_timestamp") or row.get("published_date")
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None and parsed.utcoffset() is not None:
        return {
            "source_timestamp": parsed,
            "source_time_raw": raw[:256],
            "source_time_availability": "known",
            "timezone_state": "resolved",
        }
    return {"source_time_raw": raw[:256]}


def _coverage(operation: str, text: str, error: bool) -> str:
    if error:
        return "failed"
    if not text.strip():
        return "omitted_by_text_budget"
    if operation == "search":
        return "search_snippet"
    # A warning heuristic only: absence of navigation does not verify a report body.
    links = re.findall(r"\[[^\]]*\]\([^)]*\)", text)
    if len(links) >= 10 and sum(map(len, links)) > len(text) * 0.5:
        return "navigation_heavy"
    return "extracted_text_unverified"


def research_tools(
    store: EvidenceStore,
    configured_backend: HermesResearchBackend | None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    clock = clock or (lambda: datetime.now(UTC))

    async def invoke(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if configured_backend is None:
            return ToolResult(
                status="unavailable", reason="No public research backend selected"
            ).model_dump(mode="json")
        try:
            response = await configured_backend.request({"operation": operation, **payload})
            if operation == "search":
                if response.get("success") is False:
                    return ToolResult(
                        status="unavailable", reason="Public search failed"
                    ).model_dump(mode="json")
                if response.get("success") is not True or not isinstance(
                    response.get("data"), dict
                ):
                    raise ValueError
                rows = response["data"].get("web")
                limit = payload["limit"]
            else:
                rows, limit = response.get("results"), len(payload["urls"])
            if not isinstance(rows, list) or len(rows) > limit:
                raise ValueError
            urls = [public_url(row.get("url")) for row in rows if isinstance(row, dict)]
            if len(urls) != len(rows) or len(set(urls)) != len(urls):
                raise ValueError
            if operation == "fetch":
                if set(urls) - set(payload["urls"]):
                    raise ValueError
                rows += [{"url": url, "error": True} for url in payload["urls"] if url not in urls]
            remaining, records, successful = configured_backend.config.max_chars, [], 0
            fetched = clock()
            for row in rows:
                error = bool(row.get("error"))
                text = (
                    row.get("description") or row.get("content")
                    if operation == "search"
                    else row.get("raw_content") or row.get("content")
                ) or ""
                if not isinstance(text, str):
                    raise ValueError
                available = row.get("available_chars", len(text))
                if type(available) is not int or available < len(text):
                    raise ValueError
                # An empty bridge slice is not a provider failure when source text existed.
                error = error or (operation == "fetch" and (
                    bool(row.get("source_empty")) or (not text.strip() and available == 0)
                ))
                text = "" if error else text
                kept, remaining = text[:remaining], max(0, remaining - len(text))
                original = row.get("original_chars", len(text))
                if type(original) is not int or original < len(text):
                    raise ValueError
                original_hash = row.get("content_sha256")
                if not isinstance(original_hash, str) or not re.fullmatch(
                    "[a-f0-9]{64}", original_hash
                ):
                    original_hash = hashlib.sha256(text.encode()).hexdigest()
                upstream_truncated = (
                    bool(row.get("upstream_truncated", row.get("truncated")))
                    or original > available
                )
                budget_truncated = not error and (
                    bool(row.get("budget_truncated")) or len(kept) < available
                )
                retry_guidance = None
                if operation == "fetch":
                    if error:
                        retry_guidance = (
                            "Source extraction failed; cause not established. "
                            "Try a direct public document URL or another source."
                        )
                    elif budget_truncated:
                        retry_guidance = (
                            "Fetch this single URL to give it the full tool text budget. "
                            "If upstream extraction is also truncated, try a more specific "
                            "direct document URL; whole-document coverage is not guaranteed."
                        )
                    elif upstream_truncated:
                        retry_guidance = (
                            "Upstream extraction is partial; try a more specific direct "
                            "document URL. Retrying cannot guarantee whole-document coverage."
                        )
                content = {
                    "operation": operation,
                    "title": str(row.get("title") or "")[:500],
                    "text": kept,
                    "original_chars": original,
                    "original_content_sha256": original_hash,
                    "content_hash_scope": (
                        "available_extracted_text_before_bridge_budget"
                        if row.get("content_hash_scope")
                        == "available_extracted_text_before_bridge_budget"
                        else "available_backend_text"
                    ),
                    "truncated": bool(row.get("truncated")) or len(kept) < original,
                    "error": "Source extraction failed" if error else None,
                    "extraction_coverage": _coverage(operation, kept, error),
                    "document_verified": False,
                }
                if operation == "fetch":
                    content.update({
                        "available_chars": available,
                        "returned_chars": len(kept),
                        "upstream_truncated": upstream_truncated,
                        "budget_truncated": budget_truncated,
                        "retry_guidance": retry_guidance,
                    })
                record = EvidenceRecord(
                    evidence_id="pending",
                    source="hermes_public_web",
                    fetched_at=fetched,
                    source_url=public_url(row["url"]),
                    delay_state="unknown",
                    content=content,
                    content_hash=hashlib.sha256(kept.encode()).hexdigest(),
                    provenance=("Tavily",),
                    **_source_time(row),
                ).model_dump(mode="json")
                record.pop("evidence_id")
                digest = hashlib.sha256(
                    json.dumps(
                        record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                    ).encode()
                ).hexdigest()
                record["evidence_id"] = "public-web:" + digest
                records.append(record)
                successful += not error
            refs = tuple(store.put("evidence", record) for record in records)
            status = "success" if successful or not records else "unavailable"
            return ToolResult(
                status=status,
                reason=None if status == "success" else "Source extraction failed",
                data={
                    "results": [
                        {"record_ref": ref, "source_url": record["source_url"], **record["content"]}
                        for ref, record in zip(refs, records, strict=True)
                    ],
                    "results_truncated": bool(response.get("results_truncated")),
                    "source_time_note": "Fetch time is not observation or publication time",
                    "content_note": (
                        "Retrieved text is not verified document or financial-body coverage. "
                        "Check extraction_coverage and truncation before citing; navigation-heavy "
                        "or omitted text does not establish financial claims."
                    ),
                },
                evidence_refs=refs,
                result_refs=refs,
            ).model_dump(mode="json")
        except ResearchError as exc:
            return ToolResult(status=exc.status, reason=exc.reason).model_dump(mode="json")
        except (ValueError, TypeError, KeyError, InvalidRecord):
            return ToolResult(
                status="invalid_data", reason="Invalid public research result"
            ).model_dump(mode="json")

    async def public_search(query: str, limit: int = 5) -> dict[str, Any]:
        """Search public sources (1–5 hits); source times remain unknown unless provided."""
        if type(limit) is not int or not 1 <= limit <= 5:
            return ToolResult(
                status="invalid_data",
                reason="Search limit must be an integer from 1 to 5; retry with limit=5"
            ).model_dump(mode="json")
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            return ToolResult(
                status="invalid_data",
                reason="Search query must be nonempty text of at most 2000 characters"
            ).model_dump(mode="json")
        return await invoke("search", {"query": query, "limit": limit})

    async def fetch_sources(urls: list[str]) -> dict[str, Any]:
        """Fetch 1–5 public URLs as Markdown without LLM processing; total text cap 12,000."""
        try:
            if not isinstance(urls, list) or not 1 <= len(urls) <= 5:
                raise ValueError
            selected = [public_url(url) for url in urls]
            if len(set(selected)) != len(selected):
                raise ValueError
        except (ValueError, TypeError):
            return ToolResult(
                status="invalid_data",
                reason="Provide 1 to 5 distinct public HTTP(S) URLs without credentials"
            ).model_dump(mode="json")
        return await invoke("fetch", {"urls": selected})

    return {"public_search": public_search, "fetch_sources": fetch_sources}
