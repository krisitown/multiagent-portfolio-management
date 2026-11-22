"""Dalio construction policy and independent SDK conversation."""

from strands import Agent

from portfolio_management.types import ExpertResult, RoleContext

from .definition import RoleDefinition
from .policies import READ_TOOLS, specialist_context, specialist_tools

DEFINITION = RoleDefinition(
    name="dalio",
    base_tools=READ_TOOLS | {"request_research"},
    output_type=ExpertResult,
    tools_for_config=specialist_tools,
    private_context="council_references",
    private_expert=True,
    checked_author=True,
    council_author=True,
    configure_run_data=specialist_context,
)


def build_dalio(context: RoleContext) -> Agent:
    from .base import build_agent

    return build_agent(DEFINITION, context)
