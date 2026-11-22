"""Offline SDK binding checks; connected provider probes belong to tool verification."""

import json
from collections import deque
from pathlib import Path

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_explorer_flow import explorer_config
from portfolio_management.team import run_team
from portfolio_management.types import TeamDependencies, TeamRequest


async def test_explorer_default_tools_are_exact_ready_three_without_fetch(tmp_path):
    lead = ScriptModel(
        output("explore", question="Public shortlisting"), {"text": "Offline binding only"}
    )
    explorer = ScriptModel({"text": "No retrieval attempted in binding fixture"})
    models = deque([lead, explorer])
    result = await run_team(
        TeamRequest(question="Research", mode="combined"),
        config=explorer_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    names = {tool["name"] for tool in explorer.calls[0]["tools"]}
    assert names == {"public_search", "market_statistics", "options_liquidity"}
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert set(manifest["tool_specs"]["explorer"]) == names


async def test_team_uses_explorer_specific_factory_not_analyst_search(tmp_path, monkeypatch):
    import portfolio_management.team as team

    selected = []

    async def market_statistics(series: list[str]):
        selected.append(series)
        return {"status": "success", "fixture": "Explicit offline binding, no market evidence"}

    seen = []

    def factory(store, market_provider, research_backend, clock):
        seen.append((store, market_provider, research_backend, clock))
        return {"market_statistics": market_statistics}

    monkeypatch.setattr(team, "explorer_tools", factory)
    lead = ScriptModel(output("explore", question="Context"), {"text": "Offline only"})
    explorer = ScriptModel(
        output("market_statistics", series=["SP500"]), {"text": "Fixture result"}
    )
    models = deque([lead, explorer])
    deps = TeamDependencies(model_factory=lambda *_: models.popleft())
    await run_team(
        TeamRequest(question="Research", mode="combined"),
        config=explorer_config(tmp_path),
        deps=deps,
    )
    assert selected == [["SP500"]]
    assert len(seen) == 1
    assert seen[0][1:] == (deps.market_provider, deps.research_backend, deps.clock)
