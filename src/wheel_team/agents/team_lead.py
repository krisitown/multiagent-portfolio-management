"""Team lead: decides assignments, consultation content, follow-up and stopping."""

from strands import Agent

from wheel_team.types import RoleContext

from .base import build_role


def build_team_lead(context: RoleContext) -> Agent:
    return build_role("team_lead", context)
