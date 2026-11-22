"""CLI connects only the selected shared collector; no process executes in these tests."""

import json
from pathlib import Path

from tests.unit.test_market_spark import settings
from portfolio_management.cli import load_team_config, main


def test_cli_constructs_local_market_backend_without_ssh_credentials(tmp_path, capsys):
    private = settings(tmp_path)
    for field in ("host", "user", "identity_file", "host_key_alias"):
        private.pop(field)
    private["transport"] = "local"
    private["capture_root"] = str(private["capture_root"])
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "model": {"model_id": "offline"},
                "prompts": {"mode": "development"},
                "market_backend": private,
            }
        )
    )
    assert (
        main(
            [
                "ask",
                "--question",
                "Inspect selected backend",
                "--mode",
                "education",
                "--config",
                str(config_file),
                "--output",
                str(tmp_path / "runs"),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    manifest = json.loads(Path(output["artifacts"]["manifest"]).read_text())
    market = manifest["market_provider"]
    assert market["mode"] == "connected"
    assert market["coverage"]["observed_company_ids"] == []
    assert set(market["capabilities"]) == {
        "fundamentals",
        "financial_statements",
        "events",
        "underlying_quotes",
        "underlying_history",
        "option_chains",
        "relevant_news",
    }
    assert not (tmp_path / "captures").exists()
    assert "/trusted/" not in json.dumps(manifest)


def test_config_resolves_local_capture_and_identity_paths_relative_to_config(tmp_path):
    private = settings(tmp_path)
    private["identity_file"] = "keys/market-key"
    private["capture_root"] = "captures"
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"model": {"model_id": "offline"}, "market_backend": private})
    )
    selected = load_team_config(config_file).market_backend
    assert selected.identity_file == tmp_path / "keys/market-key"
    assert selected.capture_root == tmp_path / "captures"


def test_cli_labels_explicit_openai_execution_as_team_mode(tmp_path, capsys, monkeypatch):
    from portfolio_management import cli
    from portfolio_management.types import TeamResult

    async def bounded_run(request, *, config, deps):
        # The expensive external orchestration seam is replaced; assert CLI-selected metadata.
        return TeamResult(
            summary="Metadata only", artifacts={"evaluation_mode": deps.evaluation_mode}
        )

    monkeypatch.setattr(cli, "run_team", bounded_run)
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "model": {
                    "provider": "openai",
                    "model_id": "explicit-model",
                    "endpoint": "https://example.invalid/v1",
                    "api_key_env": "TEST_ENV_REFERENCE",
                    "allow_inference_network": True,
                },
                "prompts": {"mode": "development"},
            }
        )
    )
    assert (
        main(
            [
                "ask",
                "--question",
                "Inspect selected metadata",
                "--mode",
                "education",
                "--config",
                str(config_file),
                "--output",
                str(tmp_path / "runs"),
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert output["artifacts"]["evaluation_mode"] == "team"
