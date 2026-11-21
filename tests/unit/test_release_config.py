"""Operator-specific paths and credentials must stay outside the public release."""
import importlib.util
import json
from pathlib import Path

import pytest


def module():
    path = Path(__file__).parents[2] / "scripts/configure.py"
    spec = importlib.util.spec_from_file_location("release_config", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_secret_is_a_reference_and_paths_come_from_environment(tmp_path):
    env = {
        "MODEL_NAME": "fictional-model",
        "MODEL_BASE_URL": "https://model.example/v1",
        "MODEL_API_KEY": "secret-never-serialize",
        "RUN_OUTPUT_DIR": str(tmp_path / "runs"),
        "CONTINUITY_DIR": str(tmp_path / "memory"),
        "MODEL_TOKENIZER_PATH": str(tmp_path / "tokenizer"),
    }
    config = module().make_config(env)
    assert config["model"]["model_id"] == "fictional-model"
    assert config["model"]["api_key_env"] == "MODEL_API_KEY"
    assert "secret-never-serialize" not in json.dumps(config)
    assert config["continuity"]["root"] == env["CONTINUITY_DIR"]
    assert config["fact_check"]["enabled"] is True
    assert Path(config["prompts"]["manifest_path"]).is_absolute()


def test_connector_requires_complete_explicit_configuration():
    with pytest.raises(ValueError, match="all three"):
        module().make_config({"RESEARCH_PYTHON": "/operator/python"})


def test_no_private_services_are_enabled_implicitly():
    config = module().make_config({})
    assert config.get("continuity") is None
    assert config.get("research_backend") is None
    assert config.get("market_backend") is None
    assert config["model"]["api_key_env"] == "MODEL_API_KEY"


def test_final_report_check_stays_enabled_without_optional_tokenizer():
    config = module().make_config({})
    assert config["fact_check"]["enabled"] is True
    assert "tokenizer_path" not in config["fact_check"]
