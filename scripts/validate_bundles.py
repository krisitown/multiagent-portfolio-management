"""Validate all public bundle hashes and caller baseline bindings, offline."""
import json
from pathlib import Path

from wheel_team.config import PromptSet
from wheel_team.knowledge_loader import load_knowledge
from wheel_team.prompt_loader import load_caller_amendments, load_prompt

ROOT = Path(__file__).resolve().parents[1]


def main():
    prompts = {}
    for directory, filename in [
        ("council", "role-prompts.json"),
        ("checker", "manifest.json"),
        ("continuity", "continuity-prompts.json"),
    ]:
        path = ROOT / "prompts" / directory / filename
        manifest = json.loads(path.read_text())
        selected = PromptSet(manifest_path=path)
        for role in manifest["roles"]:
            prompts[role] = load_prompt(role, selected)
    council = {k: v for k, v in prompts.items() if k not in {"fact_checker", "continuity"}}
    load_caller_amendments(ROOT / "prompts/council/caller-amendments.json", council)
    knowledge = load_knowledge(ROOT / "prompts/knowledge/knowledge-runtime.json")
    print(f"Validated {len(prompts)} role prompts and {len(knowledge.modules)} knowledge modules.")


if __name__ == "__main__":
    main()
