"""Thin private portfolio_manager constructor."""

from strands import Agent

from wheel_team.types import RoleContext

from .base import build_role


def build_portfolio_manager(context: RoleContext) -> Agent:
    return build_role("portfolio_manager", context)
