"""Real SDK author tools are explicit consultations with no host semantic enforcement."""

import hashlib
import json
from collections import deque
from pathlib import Path

import pytest

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_direct_text_handoff import direct_config
from wheel_team.config import FactCheckConfig, PromptSet
from wheel_team.team import run_team
from wheel_team.types import TeamDependencies, TeamRequest


def checked_config(tmp_path):
    prompt = tmp_path / "checker.md"
    prompt.write_text("General mechanics knowledge. Respond with ordinary reasoning feedback.")
    manifest = tmp_path / "checker-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "prompt_set_id": "offline-checker",
                "version": "1",
                "status": "development",
                "roles": {
                    "fact_checker": {
                        "path": prompt.name,
                        "content_hash": hashlib.sha256(prompt.read_bytes()).hexdigest(),
                    }
                },
            }
        )
    )
    return direct_config(tmp_path).model_copy(
        update={
            "fact_check": FactCheckConfig(
                enabled=True, prompts=PromptSet(manifest_path=manifest, mode="development")
            )
        }
    )


class Capture(ScriptModel):
    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.system = system_prompt
        async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
            yield event


async def test_omitting_tool_returns_unchanged_with_no_automatic_checks_or_guards(tmp_path):
    lead = ScriptModel(
        output("research_batch", jobs=[{"job_id": "a", "question": "inspect"}]),
        output("consult_expert"),
        {"text": "Final unchanged"},
    )
    analyst = ScriptModel({"text": "Author chose not to check"})
    expert = ScriptModel({"text": "Expert chose not to check"})
    models = deque([lead, analyst, expert])
    result = await run_team(
        TeamRequest(question="Review", mode="education"),
        config=checked_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert not models and result.completion_reason == "complete"
    assert result.report_markdown == "Final unchanged"
    assert not list(Path(result.artifacts["fact_checks"]).glob("**/call-*.json"))
    for model, allowed in [(lead, False), (analyst, True), (expert, True)]:
        assert ("fact_check" in {t["name"] for t in model.calls[0]["tools"]}) is allowed


async def test_author_explicitly_repairs_and_rechecks_natural_feedback(tmp_path):
    lead = Capture(
        output("research_batch", jobs=[{"job_id": "a", "question": "inspect"}]),
        {"text": "Final unchanged"},
    )
    author = Capture(
        output("fact_check", message="2×4×100 = 8000"),
        output("fact_check", message="2×4×100 = 800", assumptions=["No fees"]),
        {"text": "Funding 800, assuming no fees."},
    )
    first = Capture({"text": "Arithmetic correction: 2×4×100 = 800."})
    second = Capture({"text": "Under your no-fees assumption, 800 follows."})
    models = deque([lead, author, first, second])
    configs = []

    def factory(cfg, *_):
        configs.append(cfg)
        return models.popleft()

    cfg = checked_config(tmp_path)
    cfg = cfg.model_copy(
        update={"model": cfg.model.model_copy(update={"max_output_tokens": 65536})}
    )
    result = await run_team(
        TeamRequest(
            question="PRIVATE OWNER REQUEST",
            mode="education",
            portfolio_text="PRIVATE PORTFOLIO",
            owner_context="PRIVATE OWNER CONTEXT",
        ),
        config=cfg,
        deps=TeamDependencies(model_factory=factory),
    )
    assert not models and result.completion_reason == "complete"
    assert [c.max_output_tokens for c in configs] == [65536, 65536, 4096, 4096]
    records = [
        json.loads(p.read_text())
        for p in sorted(Path(result.artifacts["fact_checks"]).glob("*/call-*.json"))
    ]
    assert [r["packet"]["message"] for r in records] == ["2×4×100 = 8000", "2×4×100 = 800"]
    for checker in [first, second]:
        assert len(checker.calls) == 1 and not checker.calls[0]["tools"]
        assert len(checker.calls[0]["messages"]) == 1
        assert "PRIVATE" not in json.dumps(checker.calls) + checker.system
    assert "Arithmetic correction" in json.dumps(author.calls[1]["messages"])
    assert "fact_check_contract" not in lead.system and "fact_check_contract" in author.system


@pytest.mark.parametrize("role", ["analyst", "wheel_expert"])
async def test_only_expert_can_explicitly_select_original_private_input(tmp_path, role):
    call = output(
        "fact_check", message="Check selected original", evidence_refs=["input:portfolio"]
    )
    if role == "analyst":
        lead = ScriptModel(
            output("research_batch", jobs=[{"job_id": "a", "question": "inspect"}]),
            {"text": "Done"},
        )
    else:
        lead = ScriptModel(output("consult_expert"), {"text": "Done"})
    author = ScriptModel(call, {"text": "Author final"})
    checker = ScriptModel({"text": "Original received; limitations remain."})
    models = deque([lead, author] + ([checker] if role == "wheel_expert" else []))
    result = await run_team(
        TeamRequest(
            question="Review",
            mode="education",
            portfolio_text="Exact original private portfolio",
            owner_context="UNSELECTED OWNER CONTEXT",
        ),
        config=checked_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    record = json.loads(
        next(Path(result.artifacts["fact_checks"]).glob("*/call-*.json")).read_text()
    )
    assert result.completion_reason == "complete" and not models
    if role == "analyst":
        assert "unauthorized" in record["result"]["error"]
    else:
        assert record["packet"]["evidence"][0]["body"] == "Exact original private portfolio"
        assert "UNSELECTED OWNER CONTEXT" not in json.dumps(checker.calls)


async def test_explorer_cannot_get_checker_through_override(tmp_path):
    lead = ScriptModel(output("explore", question="Public context"), {"text": "Done"})
    explorer = Capture({"text": "Explorer original"})
    models = deque([lead, explorer])
    cfg = checked_config(tmp_path).model_copy(
        update={"explorer_enabled": True, "max_expert_consultations": 1}
    )
    await run_team(
        TeamRequest(question="Review", mode="education"),
        config=cfg,
        deps=TeamDependencies(
            model_factory=lambda *_: models.popleft(),
            tool_overrides={"explorer": {"fact_check": lambda: {"bad": True}}},
        ),
    )
    assert not models and len(explorer.calls) == 1
    assert "fact_check" not in {t["name"] for t in explorer.calls[0]["tools"]}
    assert "fact_check_contract" not in explorer.system
