"""Lightweight explorer with a separate SDK conversation and injected research tools."""

from strands import Agent

from wheel_team.types import RoleContext

from .base import build_role


def build_explorer(context: RoleContext) -> Agent:
    return build_role("explorer", context)
