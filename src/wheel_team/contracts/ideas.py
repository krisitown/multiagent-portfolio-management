"""Reviewed ideas and honest candidate slots, without a strategy recipe."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from .common import Finding, PublicModel, RecordId
from .portfolio import ContractIdentity


class CandidateStatus(StrEnum):
    READY = "Ready for user review"
    CONDITION = "Do not execute now — condition"
    UNVERIFIED = "Do not execute now — unverified"


class QualityAssessment(PublicModel):
    assessment_id: RecordId
    company_id: RecordId
    assessed_at: AwareDatetime
    conclusion: Literal["qualified", "not_qualified", "unverified"]
    rationale: str
    evidence_refs: tuple[RecordId, ...] = ()
    strengths: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()


class Alternative(PublicModel):
    description: str
    rationale: str
    contract: ContractIdentity | None = None
    additional_exposure: str | None = None
    management_burden: str | None = None
    evidence_refs: tuple[RecordId, ...] = ()
    calculation_refs: tuple[RecordId, ...] = ()


class IdeaRevision(PublicModel):
    idea_id: RecordId
    revision_id: RecordId
    company_id: RecordId
    contract: ContractIdentity | None = None
    parent_revision: RecordId | None = None
    snapshot_ref: RecordId | None = None
    evidence_refs: tuple[RecordId, ...] = ()
    calculation_refs: tuple[RecordId, ...] = ()
    quality_ref: RecordId | None = None
    thesis: str
    countercase: str
    unknowns: tuple[str, ...] = ()
    status: CandidateStatus
    reasons: tuple[str, ...] = ()
    alternatives: tuple[Alternative, ...] = ()
    author: RecordId
    task_id: RecordId
    created_at: AwareDatetime

    @model_validator(mode="after")
    def conditional_reasons(self) -> Self:
        if self.status != CandidateStatus.READY and not self.reasons:
            raise ValueError("conditional/unverified ideas require reasons")
        return self


class CandidateSlot(PublicModel):
    slot: int = Field(ge=1, le=5)
    kind: Literal["candidate", "unavailable"]
    idea: IdeaRevision | None = None
    reason: str | None = None
    warnings: tuple[str, ...] = ()
    carried_forward: bool = False
    reviewed_ref: RecordId | None = None

    @model_validator(mode="after")
    def honest_slot(self) -> Self:
        if self.kind == "candidate" and self.idea is None:
            raise ValueError("candidate slot requires a real idea")
        if self.kind == "unavailable" and (self.idea is not None or not self.reason):
            raise ValueError("unavailable slot requires reason and must not contain an idea")
        return self


class ExpertReview(PublicModel):
    review_id: RecordId
    run_id: RecordId
    round: int = Field(ge=1, le=5)
    reviewed_revision_refs: tuple[RecordId, ...]
    evidence_refs: tuple[RecordId, ...] = ()
    calculation_refs: tuple[RecordId, ...] = ()
    conclusion: str
    reasons: tuple[str, ...] = ()
    alternatives: tuple[Alternative, ...] = ()
    unknowns: tuple[str, ...] = ()
    follow_up: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    created_at: AwareDatetime
    idempotency_key: RecordId
