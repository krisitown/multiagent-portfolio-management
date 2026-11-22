"""Foreground research tools stay analyst-only and report missing dependencies."""

from tests.support.thin_fakes import fixed_clock
from tests.thin.test_entrypoint import config
from portfolio_management.team import run_team
from portfolio_management.types import (
    ExpertResult,
    ResearchJob,
    ResearchResult,
    TeamDependencies,
    TeamRequest,
    TeamResult,
)


async def test_team_binds_actual_unavailable_web_tools_only_to_analysts(tmp_path):
    observed = {}

    class Role:
        def __init__(self, role, context):
            self.role = role
            self.context = context
            observed[role] = set(context.tools)

        async def invoke_async(self, question):
            if self.role == "team_lead":
                observed["research_results"] = await self.context.tools["research_batch"](
                    [ResearchJob(job_id="web-check", question="Check dependency availability")]
                )
                await self.context.tools["consult_expert"]("Review availability", [])
                return TeamResult(summary="Dependency availability inspected")
            if self.role == "analyst":
                observed["search"] = await self.context.tools["public_search"](
                    query="labelled integration test; no market claim"
                )
                observed["fetch"] = await self.context.tools["fetch_sources"](
                    urls=["https://example.org/"]
                )
                return ResearchResult(job_id="web-check", outcome="partial", summary="Unavailable")
            return ExpertResult(assessment="Sources unavailable")

    result = await run_team(
        TeamRequest(question="Check configured dependencies", mode="education"),
        config=config(tmp_path),
        deps=TeamDependencies(role_factory=Role, clock=fixed_clock),
    )
    assert result.completion_reason == "complete"
    assert observed["search"]["status"] == "unavailable"
    assert observed["fetch"]["status"] == "unavailable"
    assert {"public_search", "fetch_sources"} <= observed["analyst"]
    for role in ("team_lead", "wheel_expert"):
        assert not {"public_search", "fetch_sources"} & observed[role]


async def test_configured_research_dependency_reaches_the_role_context(tmp_path):
    backend = object()
    observed = []

    class Lead:
        def __init__(self, role, context):
            observed.append(context.research_backend)

        async def invoke_async(self, question):
            return TeamResult(summary="Dependency passed")

    await run_team(
        TeamRequest(question="Inspect dependency", mode="education"),
        config=config(tmp_path),
        deps=TeamDependencies(role_factory=Lead, research_backend=backend, clock=fixed_clock),
    )
    assert observed == [backend]


async def test_run_manifest_preserves_public_dependency_mode_and_identity(tmp_path):
    import json
    from pathlib import Path

    from tests.support.thin_fakes import recording_factory

    class MetadataOnlyBackend:
        def describe(self):
            return {
                "mode": "connected",
                "provider": "labelled-test-identity-only",
                "source": "labelled-test-source",
                "capabilities": ["public_search", "fetch_sources"],
            }

    result = await run_team(
        TeamRequest(question="Inspect metadata only", mode="education"),
        config=config(tmp_path),
        deps=TeamDependencies(
            role_factory=recording_factory,
            research_backend=MetadataOnlyBackend(),
            clock=fixed_clock,
        ),
    )
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert manifest["market_provider"]["mode"] == "unavailable"
    assert manifest["research_backend"]["mode"] == "connected"
    assert manifest["research_backend"]["source"] == "labelled-test-source"


def test_cli_constructs_selected_research_backend_and_saves_its_public_identity(tmp_path, capsys):
    import json
    import sys
    from pathlib import Path

    from portfolio_management.cli import main

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "model": {"model_id": "offline"},
                "prompts": {"mode": "development"},
                "research_backend": {
                    "mode": "local",
                    "python": sys.executable,
                    "cwd": str(tmp_path),
                    "env_file": str(tmp_path / "not-loaded-without-tool-call.env"),
                },
            }
        )
    )
    assert (
        main(
            [
                "ask",
                "--question",
                "Explain assignment",
                "--mode",
                "education",
                "--config",
                str(config_path),
                "--output",
                str(tmp_path / "runs"),
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    manifest = json.loads(Path(summary["artifacts"]["manifest"]).read_text())
    assert manifest["research_backend"]["mode"] == "connected"
    assert manifest["research_backend"]["provider"] == "hermes_public_web"
    assert manifest["market_provider"]["mode"] == "unavailable"
    assert "not-loaded-without-tool-call.env" not in json.dumps(manifest)
