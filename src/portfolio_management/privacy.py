"""Store-scoped capabilities: public tools never hold the private store."""

from pathlib import Path
from typing import Any, cast

from portfolio_management.vault import EvidenceStore, RecordKind, RecordNotFound, record_reference


class PrivateEvidenceStore(EvidenceStore):
    """Private writes; private reviewers may additionally read public observations."""

    def __init__(self, root: Path, public_store: EvidenceStore):
        super().__init__(root)
        self.public_store = public_store

    def get(self, ref: str) -> dict[str, Any]:
        try:
            return super().get(ref)
        except RecordNotFound:
            return self.public_store.get(ref)

    def find(self, kind: str, query: dict[str, Any]) -> list[dict[str, Any]]:
        rows = self.public_store.find(kind, query) + super().find(kind, query)
        return list({record_reference(cast(RecordKind, kind), row): row for row in rows}.values())
