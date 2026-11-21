"""Buffett construction policy and independent SDK conversation."""

from strands import Agent

from wheel_team.types import ExpertResult, RoleContext

from .definition import RoleDefinition
from .policies import READ_TOOLS, specialist_context, specialist_tools

DEFINITION = RoleDefinition(
    name="buffett",
    base_tools=READ_TOOLS | {"request_research"},
    output_type=ExpertResult,
    tools_for_config=specialist_tools,
    private_context="council_references",
    private_expert=True,
    checked_author=True,
    council_author=True,
    configure_run_data=specialist_context,
)


def build_buffett(context: RoleContext) -> Agent:
    from .base import build_agent

    return build_agent(DEFINITION, context)
