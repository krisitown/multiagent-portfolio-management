"""Role-owned construction policy; the SDK assembly does not dispatch on role names."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel
from strands import Agent

from wheel_team.config import TeamConfig
from wheel_team.types import RoleContext


@dataclass(frozen=True)
class RoleDefinition:
    name: str
    base_tools: frozenset[str]
    output_type: type[BaseModel]
    tools_for_config: Callable[[TeamConfig, frozenset[str]], frozenset[str]] | None = None
    private_context: Literal["none", "inline", "council_references"] = "none"
    private_expert: bool = False
    checked_author: bool = False
    council_author: bool = False
    allow_tool_overrides: bool = False
    always_direct_text: bool = False
    configure_contract: Callable[[RoleContext, dict[str, Any]], dict[str, Any]] | None = None
    configure_run_data: Callable[[RoleContext, dict[str, Any]], None] | None = None
    install_middleware: Callable[[Agent, RoleContext], None] | None = None
    feedback_guidance: str = ""

    def allowed_tools(self, config: TeamConfig) -> frozenset[str]:
        return (
            self.tools_for_config(config, self.base_tools)
            if self.tools_for_config
            else self.base_tools
        )

    def inline_private(self, config: TeamConfig) -> bool:
        return self.private_context == "inline" or (
            self.private_context == "council_references" and not config.council_enabled
        )
