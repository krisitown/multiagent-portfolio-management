"""Frozen thin-team boundaries; financial data uses the existing domain contracts."""

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from portfolio_management.config import ModelConfig, PolicyConfig, TeamConfig
from portfolio_management.contracts import Finding, JsonValue, PortfolioSnapshot, PromptBundle

TeamMode = Literal["discovery", "portfolio", "focused", "education", "combined"]
EvaluationMode = Literal["scripted", "role", "team"]
RoleName = Literal[
    "team_lead",
    "analyst",
    "wheel_expert",
    "explorer",
    "continuity",
    "dalio",
    "buffett",
    "portfolio_manager",
]
Tool = Callable[..., Any]


class ThinModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TeamRequest(ThinModel):
    question: str = Field(min_length=1)
    mode: TeamMode
    portfolio: PortfolioSnapshot | None = None
    portfolio_text: str | None = None
    owner_context: str | None = Field(default=None, max_length=128000)
    feedback_source_ref: str | None = None
    feedback_source_attribution: str | None = None
    feedback_context: str | None = None
    feedback_record_only: bool = False
    parent_review: str | None = None
    review_kind: Literal["full", "explanation", "targeted", "refresh"] = "full"

    @model_validator(mode="after")
    def explicit_followup(self) -> "TeamRequest":
        if (self.parent_review is None) != (self.review_kind == "full"):
            raise ValueError(
                "Follow-up requires an explicit parent review and non-full review kind"
            )
        if self.owner_context is not None and not self.owner_context.strip():
            raise ValueError("Supplied owner context must contain text")
        return self

    @field_validator("question")
    @classmethod
    def nonempty_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Question must contain text")
        return value


class ResearchJob(ThinModel):
    job_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()


class SpecialistRequest(ThinModel):
    role: Literal["wheel_expert", "dalio", "buffett"]
    question: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()


class ResearchResult(ThinModel):
    job_id: str
    outcome: Literal["success", "partial", "failed", "cancelled", "budget_exhausted"]
    summary: str = ""
    idea_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    error: str | None = None


class ExpertResult(ThinModel):
    assessment: str
    shortlist_refs: tuple[str, ...] = ()
    requested_follow_up: tuple[ResearchJob, ...] = ()
    findings: tuple[Finding, ...] = ()
    error: str | None = None


class TeamResult(ThinModel):
    run_id: str = ""
    mode: TeamMode = "education"
    completion_reason: Literal["complete", "partial", "failed", "cancelled", "deadline"] = (
        "complete"
    )
    summary: str
    report_markdown: str = ""
    candidate_slots: tuple[dict[str, JsonValue], ...] = ()
    portfolio_section: str | None = None
    warnings: tuple[str, ...] = ()
    supporting_refs: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    # Filled from the run store/host after synthesis, never trusted from model output.
    supporting_records: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    portfolio_snapshot: PortfolioSnapshot | None = None
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    model_identity: dict[str, JsonValue] = Field(default_factory=dict)
    prompt_identities: dict[str, JsonValue] = Field(default_factory=dict)
    owner_policy: PolicyConfig = Field(default_factory=PolicyConfig)
    artifacts: dict[str, str] = Field(default_factory=dict)
    lineage: dict[str, JsonValue] = Field(default_factory=dict)


@dataclass
class RunBudget:
    """Shared counters only; generation accounting belongs at the model boundary."""

    max_generations: int
    max_expert_consultations: int
    deadline: datetime
    analyst_semaphore: asyncio.Semaphore
    generations: int = 0
    expert_consultations: int = 0


@dataclass
class TraceSink:
    """Append observable, caller-sanitized events; never supply hidden reasoning or secrets."""

    path: Path
    clock: Callable[[], datetime]

    best_effort_io: bool = False
    io_failures: int = 0

    def emit(self, event: str, **values: JsonValue) -> None:
        timestamp = self.clock()
        if timestamp.utcoffset() is None:
            raise ValueError("Trace clock must be timezone-aware")
        row = {"event": event, "at": timestamp.isoformat(), **values}
        payload = json.dumps(row, ensure_ascii=False) + "\n"
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(payload)
        except OSError:
            if not self.best_effort_io:
                raise
            self.io_failures += 1


@dataclass
class RoleContext:
    request: TeamRequest
    config: TeamConfig
    model: Any
    prompt: PromptBundle
    tools: dict[str, Tool]
    budget: RunBudget
    trace: TraceSink
    run_dir: Path
    market_provider: Any = None
    research_backend: Any = None
    invocation_id: str = "role"
    store: Any = None
    monotonic_deadline: float | None = None
    invocation_deadline: float | None = None
    generation_ceiling: int | None = None
    tool_override_names: frozenset[str] = frozenset()
    terminal_only: bool = False
    output_repairs_exhausted: bool = False
    prior_review: dict[str, Any] | None = None
    fact_check_amendment: str = ""


@dataclass
class TeamDependencies:
    """Concrete seams, explicitly supplied; no discovery, registration or service lifecycle."""

    role_factory: Callable[[RoleName, RoleContext], Any] | None = None
    model_factory: Callable[[ModelConfig, RunBudget, TraceSink], Any] | None = None
    market_provider: Any = None
    research_backend: Any = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    tool_overrides: dict[RoleName, dict[str, Tool]] = field(default_factory=dict)
    evaluation_mode: EvaluationMode = "scripted"
