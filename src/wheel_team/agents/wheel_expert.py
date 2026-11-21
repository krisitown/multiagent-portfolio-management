"""Wheel expert: reviews evidence and requests follow-up through the lead."""

from strands import Agent

from wheel_team.types import RoleContext

from .base import build_role


def build_wheel_expert(context: RoleContext) -> Agent:
    return build_role("wheel_expert", context)
