"""Explicit prompt selection and provider settings; secrets remain environment references."""

from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from portfolio_management.adapters.market.spark import SSHResearchConfig
from portfolio_management.adapters.research import ResearchBackendConfig
from portfolio_management.contracts import JsonValue
from portfolio_management.contracts.common import FinancialDecimal


class PromptSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    manifest_path: Path | None = None
    mode: Literal["production", "development"] = "production"

    @classmethod
    def development(cls) -> Self:
        return cls(mode="development")


class ModelConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: Literal["scripted", "openai"] = "scripted"
    model_id: str = Field(min_length=1)
    endpoint: str | None = None
    api_key_env: str | None = None
    allow_inference_network: bool = False
    timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    max_output_tokens: int = Field(default=4096, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None = None
    enable_thinking: bool | None = Field(default=None, strict=True)
    include_reasoning: bool | None = Field(default=None, strict=True)
    script: JsonValue = None

    @model_validator(mode="after")
    def configured_endpoint(self) -> Self:
        if self.provider == "openai":
            if not self.endpoint or not self.api_key_env:
                raise ValueError("OpenAI inference requires endpoint and api_key_env reference")
            parsed = urlsplit(self.endpoint)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError("inference endpoint must be an HTTP(S) URL")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError(
                    "inference endpoint must not contain credentials, query, or fragment"
                )
        return self


class PolicyConfig(BaseModel):
    """Owner-supplied advisory limits; absent requirements are never a pass."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    max_issuer_weight: FinancialDecimal | None = Field(default=None, ge=0)
    max_gross_assignment_to_nav: FinancialDecimal | None = Field(default=None, ge=0)
    max_snapshot_age_seconds: int | None = Field(default=None, ge=0, strict=True)
    max_capacity_age_seconds: int | None = Field(default=None, ge=0, strict=True)
    max_quote_age_seconds: int | None = Field(default=None, ge=0, strict=True)
    required_capacity_fields: (
        tuple[
            Literal["buying_power", "maintenance_margin", "excess_liquidity", "initial_margin"], ...
        ]
        | None
    ) = None


class FinalizationConfig(BaseModel):
    """Optional capacity held back for later roles within the unchanged run limits."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    analyst_terminal_seconds: float = Field(default=0, ge=0, allow_inf_nan=False, strict=True)
    analyst_reserve_seconds: float = Field(default=0, ge=0, allow_inf_nan=False, strict=True)
    expert_reserve_seconds: float = Field(default=0, ge=0, allow_inf_nan=False, strict=True)
    analyst_reserve_generations: int = Field(default=0, ge=0, strict=True)
    expert_reserve_generations: int = Field(default=0, ge=0, strict=True)
    # Accepted model calls after the first failed final-output tool, not all research turns.
    max_output_repairs: int | None = Field(default=None, ge=0, strict=True)


class FactCheckConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    enabled: bool = Field(default=False, strict=True)
    reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None = None
    prompts: PromptSet = Field(default_factory=PromptSet)
    max_assessments: int = Field(default=3, ge=1, le=3, strict=True)
    max_output_tokens: int = Field(default=4096, gt=0, strict=True)
    context_window_tokens: int = Field(default=262144, gt=4096, strict=True)
    tokenizer_path: Path | None = None
    caller_amendments_path: Path | None = None

    @model_validator(mode="after")
    def checker_reserve_fits(self) -> Self:
        if self.max_output_tokens >= self.context_window_tokens:
            raise ValueError("Checker output reserve must leave room for its complete input")
        return self


class ContinuityConfig(BaseModel):
    """Explicit private durable root; omitted means no continuity capability."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    root: Path
    prompts: PromptSet = Field(default_factory=PromptSet)


class TeamConfig(BaseModel):
    feedback_media_roots: list[Path] = Field(default_factory=list)
    """Finite foreground-run settings; production prompts require explicit selection."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    model: ModelConfig
    prompts: PromptSet = Field(default_factory=PromptSet)
    output_dir: Path = Path("artifacts/thin")
    child_output_mode: Literal["typed", "direct_text"] = "direct_text"
    council_enabled: bool = Field(default=False, strict=True)
    role_models: dict[
        Literal["portfolio_manager", "wheel_expert", "dalio", "buffett"], ModelConfig
    ] = Field(default_factory=dict)
    portfolio_manager_model: ModelConfig | None = None
    portfolio_manager_reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None = None
    asymmetric_enabled: bool = Field(default=False, strict=True)
    explorer_enabled: bool = Field(default=False, strict=True)
    explorer_temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    analyst_concurrency: int = Field(default=4, gt=0, strict=True)
    max_batch_jobs: int = Field(default=32, gt=0, strict=True)
    max_expert_consultations: int = Field(default=5, ge=0, le=5, strict=True)
    max_expert_research_rounds: int = Field(default=3, ge=0, le=16, strict=True)
    max_expert_research_jobs: int = Field(default=16, ge=0, le=128, strict=True)
    require_fresh_opportunities: bool = Field(default=False, strict=True)
    max_generations: int = Field(default=100, gt=0, strict=True)
    deadline_seconds: float = Field(default=2700, gt=0, allow_inf_nan=False)
    finalization: FinalizationConfig = Field(default_factory=FinalizationConfig)
    continuity: ContinuityConfig | None = None
    fact_check: FactCheckConfig = Field(default_factory=FactCheckConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    knowledge_manifest_path: Path | None = None
    market_capture_manifest_path: Path | None = None
    research_backend: ResearchBackendConfig | None = None
    market_backend: SSHResearchConfig | None = None

    @model_validator(mode="after")
    def explicit_market_selection(self) -> Self:
        if self.require_fresh_opportunities and not self.explorer_enabled:
            raise ValueError("Fresh full-review opportunities require the Explorer")
        if self.child_output_mode == "direct_text" and self.finalization.analyst_terminal_seconds:
            raise ValueError("direct_text requires analyst_terminal_seconds=0")
        if self.market_backend is not None and self.market_capture_manifest_path is not None:
            raise ValueError("Connected market backend and capture replay are mutually exclusive")
        return self

    @model_validator(mode="after")
    def finalization_fits_run(self) -> Self:
        policy = self.finalization
        if self.explorer_enabled and self.max_expert_consultations > 1:
            raise ValueError("Explorer mode permits at most one expert consultation")
        if self.explorer_enabled and any(
            (
                policy.analyst_terminal_seconds,
                policy.analyst_reserve_seconds,
                policy.expert_reserve_seconds,
                policy.analyst_reserve_generations,
                policy.expert_reserve_generations,
            )
        ):
            raise ValueError("Explorer mode uses the shared run budget without role phase reserves")
        if policy.analyst_terminal_seconds:
            if self.analyst_concurrency != 1:
                raise ValueError("Analyst terminal phase requires analyst_concurrency=1")
            if policy.analyst_terminal_seconds >= (
                self.deadline_seconds - policy.analyst_reserve_seconds
            ):
                raise ValueError("Analyst terminal seconds must fit strictly inside analyst window")
        if not (
            policy.expert_reserve_seconds <= policy.analyst_reserve_seconds < self.deadline_seconds
        ):
            raise ValueError("Finalization time reserves must fit the run in analyst/expert order")
        if not (
            policy.expert_reserve_generations
            <= policy.analyst_reserve_generations
            < self.max_generations
        ):
            raise ValueError(
                "Finalization generation reserves must fit the run in analyst/expert order"
            )
        return self
