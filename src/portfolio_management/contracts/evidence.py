"""Time, units and provenance travel with evidence and computed quantities."""

from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from .common import (
    Availability,
    Currency,
    FinancialDecimal,
    Finding,
    JsonValue,
    PublicModel,
    RecordId,
)

ResultState = Literal[
    "success", "unavailable", "retryable_error", "permanent_error", "invalid_data"
]


class EvidenceRecord(PublicModel):
    evidence_id: RecordId
    source: RecordId
    fetched_at: AwareDatetime
    source_timestamp: AwareDatetime | None = None
    source_time_raw: str | None = None
    source_time_availability: Availability = Availability.UNKNOWN
    timezone_state: Literal["resolved", "unknown", "not_applicable"] = "unknown"
    source_timezone: str | None = None
    delay_state: Literal["live", "delayed", "end_of_day", "unknown", "not_applicable"]
    delay_seconds: int | None = Field(default=None, ge=0)
    content: JsonValue
    units: dict[str, str] = Field(default_factory=dict)
    provenance: tuple[RecordId, ...] = ()
    input_refs: tuple[RecordId, ...] = ()
    source_url: str | None = None
    content_hash: str | None = None
    coverage: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()

    @model_validator(mode="after")
    def source_time_matches_availability(self) -> Self:
        if (self.source_time_availability == Availability.KNOWN) != (
            self.source_timestamp is not None
        ):
            raise ValueError("known source time requires resolved source_timestamp")
        if self.source_timestamp is not None and self.timezone_state != "resolved":
            raise ValueError("resolved source_timestamp requires resolved timezone_state")
        return self


class MarketQuery(PublicModel):
    query_id: RecordId
    capability: RecordId
    instrument_ids: tuple[RecordId, ...] = ()
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    as_of: AwareDatetime | None = None
    cursor: str | None = None
    limit: int = Field(default=100, gt=0)


class MarketResult(PublicModel):
    query_id: RecordId
    status: ResultState
    evidence: tuple[EvidenceRecord, ...] = ()
    next_cursor: str | None = None
    complete: bool = False
    reason: str | None = None
    findings: tuple[Finding, ...] = ()


class CalculationRecord(PublicModel):
    calculation_id: RecordId
    status: ResultState
    formula: RecordId
    formula_version: RecordId
    inputs: dict[str, FinancialDecimal] = Field(default_factory=dict)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    outputs: dict[str, FinancialDecimal] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)
    currencies: dict[str, Currency] = Field(default_factory=dict)
    availability: dict[str, Availability] = Field(default_factory=dict)
    input_refs: tuple[RecordId, ...] = ()
    computed_at: AwareDatetime | None = None
    findings: tuple[Finding, ...] = ()
    reason: str | None = None

    @model_validator(mode="after")
    def quantity_availability(self) -> Self:
        present = self.inputs.keys() | self.outputs.keys()
        for name, state in self.availability.items():
            if (state == Availability.KNOWN) != (name in present):
                raise ValueError(f"availability for {name} conflicts with quantity presence")
        return self
