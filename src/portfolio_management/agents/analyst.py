"""Public analyst permissions, output contract and research middleware."""

from strands import Agent

from portfolio_management.types import ResearchResult, RoleContext

from .definition import RoleDefinition
from .policies import READ_TOOLS
from .research import install_research_middleware

DEFINITION = RoleDefinition(
    name="analyst",
    base_tools=READ_TOOLS | {"market_query", "public_search", "fetch_sources"},
    output_type=ResearchResult,
    checked_author=True,
    council_author=True,
    install_middleware=install_research_middleware,
)


def build_analyst(context: RoleContext) -> Agent:
    from .base import build_agent

    return build_agent(DEFINITION, context)
