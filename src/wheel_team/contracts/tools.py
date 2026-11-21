"""Direct callable results preserve explicit availability and failure reasons."""

from typing import Self

from pydantic import Field, model_validator

from .common import Finding, JsonValue, PublicModel, RecordId
from .evidence import ResultState


class ToolResult(PublicModel):
    status: ResultState
    data: JsonValue = None
    reason: str | None = None
    evidence_refs: tuple[RecordId, ...] = ()
    result_refs: tuple[RecordId, ...] = ()
    findings: tuple[Finding, ...] = ()
    retry_after_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    trace_ref: RecordId | None = None

    @model_validator(mode="after")
    def honest_result(self) -> Self:
        if self.status == "success" and self.data is None:
            raise ValueError(
                "success requires data (use an explicit empty object for a void result)"
            )
        if self.status != "success" and not self.reason:
            raise ValueError("non-success requires reason")
        return self
