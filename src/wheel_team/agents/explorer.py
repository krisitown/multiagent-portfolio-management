"""Explorer permissions, explicit research overrides and research middleware."""

from strands import Agent

from wheel_team.types import ResearchResult, RoleContext

from .definition import RoleDefinition
from .research import install_research_middleware

DEFINITION = RoleDefinition(
    name="explorer",
    base_tools=frozenset({"public_search", "market_statistics", "options_liquidity"}),
    output_type=ResearchResult,
    council_author=True,
    allow_tool_overrides=True,
    install_middleware=install_research_middleware,
)


def build_explorer(context: RoleContext) -> Agent:
    from .base import build_agent

    return build_agent(DEFINITION, context)
