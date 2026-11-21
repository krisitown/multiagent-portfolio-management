"""Role outputs carry claims and references, while actions remain tool calls."""

from enum import StrEnum
from typing import Literal

from pydantic import Field

from .common import PublicModel, RecordId


class AgentRole(StrEnum):
    TEAM_LEAD = "team_lead"
    WHEEL_EXPERT = "wheel_expert"
    ANALYST = "analyst"
    EXPLORER = "explorer"


class PromptBundle(PublicModel):
    prompt_id: RecordId
    role: AgentRole
    version: RecordId
    content: str
    content_hash: RecordId
    status: Literal["development", "approved"]
    owner_approval_ref: RecordId | None = None
    module_hashes: dict[str, str] = Field(default_factory=dict)
    shared_hashes: dict[str, str] = Field(default_factory=dict)
    source_path: str | None = None
