"""Thin private buffett constructor."""

from strands import Agent

from wheel_team.types import RoleContext

from .base import build_role


def build_buffett(context: RoleContext) -> Agent:
    return build_role("buffett", context)
