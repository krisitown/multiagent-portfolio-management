"""Thin private dalio constructor."""

from strands import Agent

from wheel_team.types import RoleContext

from .base import build_role


def build_dalio(context: RoleContext) -> Agent:
    return build_role("dalio", context)
