"""Read exactly the supplied snapshot; no account or filesystem discovery."""

from typing import Any

from wheel_team.contracts import PortfolioSnapshot, ToolResult
from wheel_team.vault import EvidenceStore


def portfolio_tools(store: EvidenceStore, snapshot: PortfolioSnapshot | None) -> dict[str, Any]:
    ref = store.put("snapshot", snapshot.model_dump(mode="json")) if snapshot is not None else None

    async def portfolio_read(snapshot_ref: str) -> dict[str, Any]:
        """Read the exact supplied snapshot ID or its run-local immutable reference."""
        if snapshot is None:
            return ToolResult(
                status="unavailable", reason="No portfolio snapshot was supplied"
            ).model_dump(mode="json")
        if snapshot_ref not in {snapshot.snapshot_id, ref}:
            return ToolResult(
                status="invalid_data", reason="Requested snapshot is not the supplied snapshot"
            ).model_dump(mode="json")
        assert ref is not None
        return ToolResult(status="success", data=store.get(ref), result_refs=(ref,)).model_dump(
            mode="json"
        )

    return {"portfolio_read": portfolio_read}
