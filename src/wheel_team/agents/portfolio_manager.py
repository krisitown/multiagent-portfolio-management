"""Portfolio manager owns council planning, private inputs and delegation permissions."""

from typing import Any

from strands import Agent

from wheel_team.config import TeamConfig
from wheel_team.types import ExpertResult, RoleContext

from .definition import RoleDefinition
from .policies import READ_TOOLS

BASE_TOOLS = READ_TOOLS | {"request_research", "consult_specialists"}


def allowed_tools(config: TeamConfig, base_tools: frozenset[str]) -> frozenset[str]:
    allowed = base_tools
    if config.council_enabled:
        allowed = allowed | {"option_scenarios", "explore"}
        if config.asymmetric_enabled:
            allowed = allowed | {"consult_asymmetric"}
    if config.continuity is not None:
        allowed = allowed | {"consult_continuity"}
    return allowed


def output_contract(context: RoleContext, contract: dict[str, Any]) -> dict[str, Any]:
    if context.config.council_enabled:
        contract["planning"] = (
            "Start by planning the original owner task. For full reviews, public "
            "research, explore, optional asymmetric discovery and all three broad "
            "peers are available under your direction. Request independent Wheel, "
            "Dalio and Buffett views where useful, including together in one "
            "consult_specialists call. Supply only relevant evidence_refs to peers; "
            "they can selectively read/check originals. You retain final authority "
            "and may challenge earlier DCA assumptions."
        )
    return contract


DEFINITION = RoleDefinition(
    name="portfolio_manager",
    base_tools=BASE_TOOLS,
    output_type=ExpertResult,
    tools_for_config=allowed_tools,
    private_context="inline",
    private_expert=True,
    checked_author=True,
    council_author=True,
    configure_contract=output_contract,
)


def build_portfolio_manager(context: RoleContext) -> Agent:
    from .base import build_agent

    return build_agent(DEFINITION, context)
