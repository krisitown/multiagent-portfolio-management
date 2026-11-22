"""Shared immutable, versioned public boundary types."""

from copy import deepcopy
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

RecordId = Annotated[str, Field(min_length=1, strict=True)]
RunId = RecordId
IdeaId = RecordId
RevisionId = RecordId
EvidenceId = RecordId
SnapshotId = RecordId
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$", strict=True)]
FinancialDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]


class Availability(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class CompletionReason(StrEnum):
    COMPLETE = "complete"
    DEADLINE = "deadline"
    BUDGET = "budget"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    MODEL_FAILURE = "model_failure"
    CANCELLED = "cancelled"


class PublicModel(BaseModel):
    """Freeze fields, reject unknown fields, detach arbitrary JSON input aliases.

    JSON objects are defensive copies (R2), rather than deeply frozen mappings.
    Callers should serialize a new revision instead of mutating nested JSON values.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)
    schema_version: Literal[1] = 1

    @model_validator(mode="before")
    @classmethod
    def detach_and_check_version(cls, value: Any) -> Any:
        if isinstance(value, dict):
            version = value.get("schema_version", 1)
            if type(version) is not int or version != 1:
                raise ValueError("schema_version must be the integer 1")
            return deepcopy(value)
        return value


class Finding(PublicModel):
    code: RecordId
    severity: Literal["info", "warning", "error"]
    affected_ids: tuple[RecordId, ...] = ()
    reason: Annotated[str, Field(min_length=1)]
    evidence_refs: tuple[EvidenceId, ...] = ()


class QuantityValue(PublicModel):
    availability: Availability
    value: FinancialDecimal | None = None
    unit: RecordId
    currency: Currency | None = None
    source: str | None = None
    evidence_refs: tuple[EvidenceId, ...] = ()
    reason: str | None = None

    @model_validator(mode="after")
    def value_matches_availability(self) -> Self:
        if (self.availability == Availability.KNOWN) != (self.value is not None):
            raise ValueError(
                "known values require value; unknown/unavailable values must omit value"
            )
        return self


__all__ = [
    "Availability",
    "CompletionReason",
    "Currency",
    "EvidenceId",
    "FinancialDecimal",
    "Finding",
    "IdeaId",
    "JsonValue",
    "PublicModel",
    "QuantityValue",
    "RecordId",
    "RevisionId",
    "RunId",
    "SnapshotId",
]
