"""Render operator environment references into an explicitly selected config."""
import argparse
import json
import os
from pathlib import Path

from portfolio_management.config import TeamConfig

ROOT = Path(__file__).resolve().parents[1]


def make_config(env):
    def asset(path):
        return str(ROOT / path)

    tokenizer = env.get("MODEL_TOKENIZER_PATH")
    config = {
        "model": {
            "provider": "openai",
            "model_id": env.get("MODEL_NAME", "qwen"),
            "endpoint": env.get("MODEL_BASE_URL", "http://127.0.0.1:8000/v1"),
            "api_key_env": "MODEL_API_KEY",
            "allow_inference_network": True,
            "reasoning_effort": env.get("MODEL_REASONING_EFFORT", "medium"),
            "timeout_seconds": float(env.get("RUN_DEADLINE_SECONDS", "7200")),
            "max_output_tokens": int(env.get("MODEL_MAX_OUTPUT_TOKENS", "16384")),
        },
        "prompts": {"manifest_path": asset("prompts/council/role-prompts.json")},
        "knowledge_manifest_path": asset("prompts/knowledge/knowledge-runtime.json"),
        "output_dir": str(Path(env.get("RUN_OUTPUT_DIR", "./runs")).expanduser().resolve()),
        "council_enabled": True,
        "explorer_enabled": True,
        "asymmetric_enabled": True,
        "max_expert_consultations": 1,
        "analyst_concurrency": 4,
        "max_generations": 300,
        "deadline_seconds": float(env.get("RUN_DEADLINE_SECONDS", "7200")),
        "fact_check": {
            "enabled": True,
            "max_assessments": 3,
            "max_output_tokens": 8192,
            "reasoning_effort": env.get("MODEL_REASONING_EFFORT", "medium"),
            "prompts": {"manifest_path": asset("prompts/checker/manifest.json")},
            "caller_amendments_path": asset("prompts/council/caller-amendments.json"),
        },
    }
    if tokenizer:
        config["fact_check"]["tokenizer_path"] = str(Path(tokenizer).expanduser().resolve())
    if env.get("CONTINUITY_DIR"):
        config["continuity"] = {
            "root": str(Path(env["CONTINUITY_DIR"]).expanduser().resolve()),
            "prompts": {"manifest_path": asset("prompts/continuity/continuity-prompts.json")},
        }
    connector = [env.get(k) for k in ["RESEARCH_PYTHON", "RESEARCH_CWD", "RESEARCH_ENV_FILE"]]
    if any(connector):
        if not all(connector):
            raise ValueError("Set all three RESEARCH_PYTHON, RESEARCH_CWD and RESEARCH_ENV_FILE")
        config["research_backend"] = dict(zip(["python", "cwd", "env_file"], connector))
    TeamConfig.model_validate(config)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = make_config(os.environ)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # The generated file contains operator paths; keep it private.
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(config, stream, indent=2)
        stream.write("\n")
    print("Configuration written. Secret values were not serialized.")
    if not config["fact_check"].get("tokenizer_path"):
        print("Checker enabled with conservative context counting; a local tokenizer is optional.")


if __name__ == "__main__":
    main()
