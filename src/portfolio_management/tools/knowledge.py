"""Read named modules only from an explicitly selected, hash-validated bundle."""

from pathlib import Path
from typing import Any

from portfolio_management.contracts import ToolResult
from portfolio_management.knowledge_loader import load_knowledge, read_module
from portfolio_management.vault import EvidenceStore


def knowledge_tools(store: EvidenceStore, manifest_path: Path | None = None) -> dict[str, Any]:
    frozen: dict[str, dict[str, Any]] = {}
    invalid = False
    if manifest_path is not None:
        try:
            manifest = load_knowledge(manifest_path)
            frozen = {
                module.module_id: {
                    "module": module.model_dump(mode="json"),
                    "content": read_module(manifest_path, module),
                }
                for module in manifest.modules
            }
        except ValueError:
            invalid = True

    async def knowledge_read(module_id: str) -> dict[str, Any]:
        """Read an indexed module by logical ID, preserving source labels and content."""
        if manifest_path is None:
            return ToolResult(
                status="unavailable", reason="No knowledge bundle was selected"
            ).model_dump(mode="json")
        if invalid:
            return ToolResult(
                status="invalid_data", reason="Knowledge bundle failed validation"
            ).model_dump(mode="json")
        if module_id not in frozen:
            return ToolResult(
                status="unavailable", reason="Knowledge module is not indexed"
            ).model_dump(mode="json")
        return ToolResult(status="success", data=frozen[module_id]).model_dump(mode="json")

    return {"knowledge_read": knowledge_read}
