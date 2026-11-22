"""Explicit capture selection cannot silently run with no market evidence."""

import json

from portfolio_management.cli import load_team_config, main


def test_capture_selection_resolves_relative_to_config(tmp_path):
    config_path = tmp_path / "private" / "config.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps(
            {
                "model": {"model_id": "offline"},
                "market_capture_manifest_path": "captures/selected/manifest.json",
            }
        )
    )
    loaded = load_team_config(config_path)
    assert loaded.market_capture_manifest_path == (
        config_path.parent / "captures/selected/manifest.json"
    )


def test_cli_rejects_missing_selected_capture_before_starting_run(tmp_path, capsys):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "model": {"model_id": "offline"},
                "prompts": {"mode": "development"},
                "market_capture_manifest_path": "captures/missing/manifest.json",
            }
        )
    )
    output = tmp_path / "runs"
    code = main(
        [
            "ask",
            "--question",
            "Research supplied capture",
            "--mode",
            "focused",
            "--config",
            str(config_path),
            "--output",
            str(output),
        ]
    )
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "configuration_or_output_error"
    assert not output.exists()


def test_cli_cannot_silently_ignore_an_existing_unrecognized_capture(tmp_path, capsys):
    capture_manifest = tmp_path / "capture.json"
    capture_manifest.write_text("{}")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "model": {"model_id": "offline"},
                "prompts": {"mode": "development"},
                "market_capture_manifest_path": str(capture_manifest),
            }
        )
    )
    output = tmp_path / "runs"
    assert (
        main(
            [
                "ask",
                "--question",
                "Research supplied capture",
                "--mode",
                "focused",
                "--config",
                str(config_path),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["error"] == "configuration_or_output_error"
    assert not output.exists()


def test_cli_constructs_completed_real_capture_as_explicit_replay(tmp_path, capsys):
    import os
    from pathlib import Path

    import pytest

    selected = os.environ.get("WHEEL_TEST_CAPTURE_INDEX")
    if not selected:
        pytest.skip("Select private completed capture with WHEEL_TEST_CAPTURE_INDEX")
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "model": {"model_id": "offline"},
                "prompts": {"mode": "development"},
                "market_capture_manifest_path": selected,
            }
        )
    )
    assert (
        main(
            [
                "ask",
                "--question",
                "Inspect selected replay mode",
                "--mode",
                "education",
                "--config",
                str(path),
                "--output",
                str(tmp_path / "runs"),
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    manifest = json.loads(Path(summary["artifacts"]["manifest"]).read_text())
    assert manifest["market_provider"]["mode"] == "captured_replay"
    assert manifest["market_provider"]["capture_id"] == Path(selected).parent.name
    assert manifest["market_provider"]["coverage"]["captured"] == ["KO", "MSFT", "JNJ"]
    assert manifest["research_backend"]["mode"] == "unavailable"
