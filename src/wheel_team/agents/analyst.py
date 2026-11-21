"""Independent analyst: read-only market research and explicit evidence publication."""

from strands import Agent

from wheel_team.types import RoleContext

from .base import build_role


def build_analyst(context: RoleContext) -> Agent:
    return build_role("analyst", context)
