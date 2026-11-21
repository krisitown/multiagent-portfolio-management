"""Stable public contract imports and deterministic JSON-schema export."""

import json
from pathlib import Path

from .agents import AgentRole as AgentRole
from .agents import PromptBundle as PromptBundle
from .common import Availability as Availability
from .common import CompletionReason as CompletionReason
from .common import Finding as Finding
from .common import JsonValue as JsonValue
from .common import QuantityValue as QuantityValue
from .evidence import CalculationRecord as CalculationRecord
from .evidence import EvidenceRecord as EvidenceRecord
from .evidence import MarketQuery as MarketQuery
from .evidence import MarketResult as MarketResult
from .ideas import Alternative as Alternative
from .ideas import CandidateSlot as CandidateSlot
from .ideas import CandidateStatus as CandidateStatus
from .ideas import ExpertReview as ExpertReview
from .ideas import IdeaRevision as IdeaRevision
from .ideas import QualityAssessment as QualityAssessment
from .knowledge import AuthoringProvenance as AuthoringProvenance
from .knowledge import KnowledgeManifest as KnowledgeManifest
from .knowledge import KnowledgeModule as KnowledgeModule
from .knowledge import KnowledgeSource as KnowledgeSource
from .knowledge import WorkedExample as WorkedExample
from .portfolio import BrokerCapacity as BrokerCapacity
from .portfolio import CashBalance as CashBalance
from .portfolio import CashFlow as CashFlow
from .portfolio import ContractIdentity as ContractIdentity
from .portfolio import PortfolioSnapshot as PortfolioSnapshot
from .portfolio import Position as Position
from .tools import ToolResult as ToolResult

PUBLIC_MODELS = (
    Alternative,
    AuthoringProvenance,
    BrokerCapacity,
    CalculationRecord,
    CandidateSlot,
    CashBalance,
    CashFlow,
    ContractIdentity,
    EvidenceRecord,
    ExpertReview,
    Finding,
    IdeaRevision,
    KnowledgeManifest,
    KnowledgeModule,
    KnowledgeSource,
    MarketQuery,
    MarketResult,
    PortfolioSnapshot,
    Position,
    PromptBundle,
    QualityAssessment,
    QuantityValue,
    ToolResult,
    WorkedExample,
)


def export_schemas(destination: Path) -> None:
    """Export version-1 serialization schemas (financial decimals are strings)."""
    destination.mkdir(parents=True, exist_ok=True)
    # Lazy imports preserve a pure contracts import without a config/types cycle.
    from wheel_team.config import ModelConfig, PolicyConfig, PromptSet, TeamConfig
    from wheel_team.types import ExpertResult, ResearchJob, ResearchResult, TeamRequest, TeamResult

    models = (
        *PUBLIC_MODELS,
        ModelConfig,
        PolicyConfig,
        PromptSet,
        TeamConfig,
        ExpertResult,
        ResearchJob,
        ResearchResult,
        TeamRequest,
        TeamResult,
    )
    for model in models:
        (destination / f"{model.__name__}.json").write_text(
            json.dumps(model.model_json_schema(mode="serialization"), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
