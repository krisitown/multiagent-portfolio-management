"""Readable portfolio transport preserves owner input and role isolation."""

import pytest

from tests.support.thin_sdk import ScriptModel
from tests.thin.test_agents import context
from tests.thin.test_entrypoint import snapshot
from wheel_team.agents.base import build_role
from wheel_team.types import TeamRequest

TEXT = (
    "  Portfolio α\r\nAs of 2026-10-31: quantity -2 contracts\nMargin reported, estimate unknown.\n"
)


def test_text_is_unvalidated_and_preferred_over_legacy_snapshot():
    from wheel_team.adapters.portfolio.text import readable_portfolio

    request = TeamRequest(
        question="Review", mode="portfolio", portfolio_text=TEXT, portfolio=snapshot()
    )
    assert readable_portfolio(request) == TEXT
    assert readable_portfolio(request.model_copy(update={"portfolio_text": ""})) == ""
    assert readable_portfolio(TeamRequest(question="Review", mode="portfolio")) is None


def test_legacy_snapshot_rendering_is_readable_deterministic_and_complete():
    from wheel_team.adapters.portfolio.text import readable_portfolio

    request = TeamRequest(question="Review", mode="portfolio", portfolio=snapshot())
    text = readable_portfolio(request)
    assert text == readable_portfolio(request)
    assert "snapshot_id: synthetic-snapshot" in text
    assert "as_of:" in text
    assert "positions:" in text
    assert '"snapshot_id"' not in text


@pytest.mark.parametrize("role", ["team_lead", "analyst", "wheel_expert"])
def test_lead_and_expert_tasks_receive_original_text_once(tmp_path, role):
    ctx = context(tmp_path, role, ScriptModel())
    ctx.request = TeamRequest(
        question="Review", mode="portfolio", portfolio_text=TEXT, portfolio=snapshot()
    )
    agent = build_role(role, ctx)
    assert TEXT not in agent.system_prompt
    supplied = "".join(b.get("text", "") for m in agent.messages for b in m["content"])
    assert supplied.count(TEXT) == (1 if role in {"team_lead", "wheel_expert"} else 0)
    assert '"portfolio_snapshot":' not in agent.system_prompt
    if role != "wheel_expert":
        assert "account_alias:" not in agent.system_prompt


def test_cli_preserves_utf8_and_newlines(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from wheel_team import cli

    path = tmp_path / "portfolio.txt"
    path.write_bytes(TEXT.encode("utf-8"))
    seen = []

    async def run(request, **kwargs):
        seen.append(request)
        return SimpleNamespace(
            report_markdown="Done", run_id="offline", completion_reason="complete", artifacts={}
        )

    monkeypatch.setattr(
        cli, "load_team_config", lambda _: context(tmp_path, "analyst", ScriptModel()).config
    )
    monkeypatch.setattr(cli, "run_team", run)
    assert (
        cli.main(
            [
                "ask",
                "--question",
                "Review",
                "--mode",
                "portfolio",
                "--config",
                str(tmp_path / "unused"),
                "--portfolio-text",
                str(path),
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert seen[0].portfolio_text == TEXT


def test_explorer_tools_require_explicit_override_and_share_research_contract(tmp_path):
    ctx = context(tmp_path, "analyst", ScriptModel())
    # Development fixture only; owner workshop owns production explorer assets.
    ctx.prompt = ctx.prompt.model_copy(update={"role": "explorer"})
    ctx.request = TeamRequest(question="Explore", mode="portfolio", portfolio_text=TEXT)

    def discovery(query: str = "") -> str:
        """Read public discovery fixture."""
        return query

    ctx.tools = {"public_search": discovery, "custom_discovery": discovery}
    agent = build_role("explorer", ctx)
    assert TEXT not in agent.system_prompt
    assert '"tool_name": "ResearchResult"' in agent.system_prompt
    assert "custom_discovery" not in agent.tool_names
    assert "public_search" in agent.tool_names
    ctx.tool_override_names = frozenset({"custom_discovery"})
    agent = build_role("explorer", ctx)
    assert "custom_discovery" in agent.tool_names


@pytest.mark.parametrize("payload", [None, b"\xff"])
def test_cli_text_io_errors_remain_real_errors(tmp_path, monkeypatch, capsys, payload):
    from wheel_team import cli

    path = tmp_path / "portfolio.txt"
    if payload is not None:
        path.write_bytes(payload)
    monkeypatch.setattr(
        cli, "load_team_config", lambda _: context(tmp_path, "analyst", ScriptModel()).config
    )
    assert (
        cli.main(
            [
                "ask",
                "--question",
                "Review",
                "--mode",
                "portfolio",
                "--config",
                str(tmp_path / "unused"),
                "--portfolio-text",
                str(path),
                "--output",
                str(tmp_path),
            ]
        )
        == 2
    )
    assert "configuration_or_output_error" in capsys.readouterr().err
