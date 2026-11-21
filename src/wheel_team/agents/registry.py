"""Explicit role inventory, derived from role-owned definitions; no plugin discovery."""

from strands import Agent

from wheel_team.config import TeamConfig
from wheel_team.types import RoleContext

from . import (
    analyst,
    buffett,
    continuity,
    dalio,
    explorer,
    portfolio_manager,
    team_lead,
    wheel_expert,
)

ROLE_MODULES = (team_lead, analyst, wheel_expert, dalio, buffett, portfolio_manager, explorer)
DEFINITIONS = {module.DEFINITION.name: module.DEFINITION for module in (*ROLE_MODULES, continuity)}
ROLE_TOOLS = {module.DEFINITION.name: module.DEFINITION.base_tools for module in ROLE_MODULES}
PRIVATE_EXPERTS = frozenset(name for name, role in DEFINITIONS.items() if role.private_expert)
CHECKED_AUTHORS = frozenset(name for name, role in DEFINITIONS.items() if role.checked_author)


def role_tools(role: str, config: TeamConfig) -> frozenset[str]:
    return DEFINITIONS[role].allowed_tools(config)


def build_role(role: str, context: RoleContext) -> Agent:
    from .base import build_agent

    if context.prompt.role != role:
        raise ValueError("prompt role must match role constructor")
    if context.model is None:
        raise ValueError("A configured or explicitly injected SDK model is required")
    return build_agent(DEFINITIONS[role], context)
