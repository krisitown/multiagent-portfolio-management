"""Private file-memory role: no market/delegation tools or typed output requirement."""

from typing import Any

from strands import Agent

from wheel_team.types import ExpertResult, RoleContext

from .definition import RoleDefinition


def output_contract(context: RoleContext, contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "direct_text",
        "advisory": (
            "Finish with ordinary text. File access and save receipts are recorded by the host."
        ),
    }


DEFINITION = RoleDefinition(
    name="continuity",
    base_tools=frozenset(
        {"continuity_list", "continuity_read", "continuity_search", "continuity_write"}
    ),
    output_type=ExpertResult,
    always_direct_text=True,
    configure_contract=output_contract,
)


def build_continuity(context: RoleContext) -> Agent:
    from .base import build_agent

    return build_agent(DEFINITION, context)
