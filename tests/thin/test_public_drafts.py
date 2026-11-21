"""Completed public prose survives a failed native conversion without becoming a result."""

import asyncio
import hashlib
import json
import time
from pathlib import Path

from strands.hooks.events import AfterModelCallEvent

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_agents import context
from tests.thin.test_entrypoint import config
from wheel_team.models import RunHooks
from wheel_team.team import run_team
from wheel_team.types import TeamDependencies, TeamRequest


async def test_completed_public_lead_text_survives_deadline_as_unvalidated_draft(tmp_path):
    text = "  Draft α\n```\nUnverified evidence:missing\n```\n"
    model = ScriptModel({"text": text}, asyncio.Event())
    result = await run_team(
        TeamRequest(question="Assess", mode="focused"),
        config=config(tmp_path).model_copy(update={"deadline_seconds": 0.1}),
        deps=TeamDependencies(model_factory=lambda *_: model),
    )
    assert result.completion_reason == "deadline"
    assert result.summary == "Run deadline reached before final synthesis."
    assert result.supporting_refs == () and result.candidate_slots == ()
    draft_path = Path(result.artifacts["unvalidated_lead_draft"])
    metadata_path = Path(result.artifacts["unvalidated_lead_draft_metadata"])
    assert draft_path.read_text() == text
    assert draft_path.stat().st_mode & 0o777 == 0o600
    assert metadata_path.stat().st_mode & 0o777 == 0o600
    metadata = json.loads(metadata_path.read_text())
    assert metadata["validation_status"] == "unvalidated"
    assert metadata["role"] == "team_lead" and metadata["generation"] == 1
    assert metadata["stop_reason"] == "end_turn"
    assert metadata["text_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert metadata["text_chars"] == len(text)
    report = Path(result.artifacts["report"]).read_text()
    assert "Unvalidated model draft" in report and text in report
    assert "Native TeamResult did not complete" in report
    assert result.report_markdown == report
    trace = Path(result.artifacts["trace"]).read_text()
    assert text not in trace


async def test_native_result_stays_authoritative_while_earlier_draft_is_private(tmp_path):
    text = "An earlier draft must not replace the validated result."
    model = ScriptModel({"text": text}, output("TeamResult", summary="Native final summary"))
    result = await run_team(
        TeamRequest(question="Explain", mode="education"),
        config=config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: model),
    )
    assert result.completion_reason == "complete" and result.summary == "Native final summary"
    assert Path(result.artifacts["unvalidated_lead_draft"]).read_text() == text
    assert text not in result.report_markdown
    assert "Unvalidated model draft" not in result.report_markdown


async def test_unfinished_public_stream_is_not_a_completed_draft(tmp_path):
    class Interrupted(ScriptModel):
        async def stream(self, *args, **kwargs):
            yield {"messageStart": {"role": "assistant"}}
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "unfinished public text"}}}
            await asyncio.Event().wait()

    result = await run_team(
        TeamRequest(question="Explain", mode="education"),
        config=config(tmp_path).model_copy(update={"deadline_seconds": 0.03}),
        deps=TeamDependencies(model_factory=lambda *_: Interrupted()),
    )
    assert result.completion_reason == "deadline"
    assert "unvalidated_lead_draft" not in result.artifacts
    assert "unfinished public text" not in result.report_markdown
    assert not list(Path(result.artifacts["report"]).parent.glob("unvalidated-lead-draft-*"))


async def test_each_completed_response_is_preserved_without_reasoning_or_provider_errors(tmp_path):
    ctx = context(tmp_path, "team_lead", ScriptModel())
    hooks = RunHooks(ctx.budget, ctx.trace, "team_lead", "lead", context=ctx)
    for generation, text in [(1, "First public draft\n"), (2, "Second public draft\n")]:
        hooks.active = generation, time.perf_counter()
        hooks.after_model(
            AfterModelCallEvent(
                agent=None,
                stop_response=AfterModelCallEvent.ModelStopResponse(
                    stop_reason="end_turn",
                    message={
                        "role": "assistant",
                        "content": [
                            {"text": text},
                            {"reasoningContent": {"reasoningText": {"text": "hidden secret"}}},
                        ],
                    },
                ),
            )
        )
        assert (tmp_path / f"unvalidated-lead-draft-{generation}.txt").read_text() == text
    hooks.active = 3, time.perf_counter()
    hooks.after_model(
        AfterModelCallEvent(agent=None, exception=RuntimeError("provider credential secret"))
    )
    assert not (tmp_path / "unvalidated-lead-draft-3.txt").exists()
    assert len(list(tmp_path.glob("unvalidated-lead-draft-*.txt"))) == 2
    assert all("secret" not in path.read_text() for path in tmp_path.iterdir())
