"""Completed lead narratives survive the foreground boundary without host judgment."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI
from strands.models.openai import OpenAIModel

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_agents import context
from tests.thin.test_direct_text_handoff import CutoffModel, direct_config
from tests.thin.test_entrypoint import config, snapshot
from wheel_team.agents.base import build_role
from wheel_team.cli import main
from wheel_team.config import FinalizationConfig
from wheel_team.team import run_team
from wheel_team.types import TeamDependencies, TeamRequest

NARRATIVE = (
    "  # Lead α — judgment\r\n\n"
    "Ready for review if future quotes confirm it; capacity remains unknown.\n"
    'candidate_slots="not an array"; portfolio_section=null; [evidence:missing]\n\t '
)


def text_http_model(text, finish="stop"):
    """Use the actual OpenAI/Strands protocol with only its HTTP transport replaced."""
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        # A formatting/extraction retry would mask the ordinary completion boundary.
        if len(requests) > 1:
            raise AssertionError("Ordinary final text must not require an extraction request")
        base = {
            "id": "offline",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "offline",
        }
        chunks = [
            {**base, "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]},
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        ]
        sse = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=sse + "data: [DONE]\n\n"
        )

    client = AsyncOpenAI(
        api_key="offline",
        base_url="http://offline.invalid/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    return OpenAIModel(client=client, model_id="offline"), client, requests


@pytest.mark.parametrize("mode,portfolio", [("discovery", None), ("combined", snapshot())])
async def test_normal_sdk_end_turn_is_exact_report_without_format_or_integrity_gate(
    tmp_path, mode, portfolio
):
    # Catches structured extraction, validate_result downgrades, render_result and annex appends.
    model, client, requests = text_http_model(NARRATIVE)
    try:
        result = await run_team(
            TeamRequest(question="Inspect", mode=mode, portfolio=portfolio),
            config=direct_config(tmp_path),
            deps=TeamDependencies(model_factory=lambda *_: model),
        )
    finally:
        await client.close()
    assert result.completion_reason == "complete"
    assert result.summary == result.report_markdown == NARRATIVE
    assert Path(result.artifacts["report"]).read_bytes() == NARRATIVE.encode()
    assert result.findings == () and result.candidate_slots == ()
    assert result.supporting_records == {} and result.warnings == ()
    saved = json.loads(Path(result.artifacts["result"]).read_text())
    assert saved["report_markdown"] == NARRATIVE
    assert saved["completion_reason"] == "complete"
    assert (saved["portfolio_snapshot"]["snapshot_id"] if portfolio else None) == (
        "synthetic-snapshot" if portfolio else None
    )
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    assert manifest["generations"] == 1
    assert "TeamResult" not in manifest["tool_specs"]["team_lead"]
    assert len(requests) == 1
    assert "TeamResult" not in {tool["function"]["name"] for tool in requests[0]["tools"]}
    assert "tool_choice" not in requests[0]
    assert not any("unvalidated_lead_draft" in key for key in result.artifacts)


@pytest.mark.parametrize("reason", ["complete", "partial"])
async def test_typed_compatibility_delivers_selected_text_and_lead_completion_unchanged(
    tmp_path, reason
):
    # Catches metadata/reference validation changing an explicitly returned lead judgment.
    model = ScriptModel(
        output(
            "TeamResult",
            summary="Alternate summary",
            report_markdown=NARRATIVE,
            completion_reason=reason,
            candidate_slots=[{"unexpected": "malformed slot", "readiness": "ready"}],
            portfolio_section=None,
            supporting_refs=["idea:missing@v1"],
            warnings=["Lead supplied limitation"],
        )
    )
    result = await run_team(
        TeamRequest(question="Inspect", mode="combined", portfolio=snapshot()),
        config=config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: model),
    )
    assert result.completion_reason == reason
    assert result.summary == "Alternate summary"
    assert result.report_markdown == NARRATIVE
    assert Path(result.artifacts["report"]).read_bytes() == NARRATIVE.encode()
    assert result.warnings == ("Lead supplied limitation",)
    assert result.findings == () and result.supporting_records == {}
    assert result.candidate_slots == ({"unexpected": "malformed slot", "readiness": "ready"},)


@pytest.mark.parametrize("typed,reason", [(False, "complete"), (True, "partial")])
def test_real_cli_stdout_is_exact_narrative_with_separate_status_paths(
    tmp_path, capsys, typed, reason
):
    # Catches JSON wrapping, headers and print()'s implicit final newline in ask stdout.
    cfg = config(tmp_path) if typed else direct_config(tmp_path)
    step = (
        output("TeamResult", summary=NARRATIVE, completion_reason=reason)
        if typed
        else {"text": NARRATIVE}
    )
    payload = cfg.model_dump(mode="json")
    payload["model"]["script"] = {"role_scripts": {"team_lead": [[step]]}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload))
    code = main(
        [
            "ask",
            "--question",
            "Inspect",
            "--mode",
            "discovery",
            "--config",
            str(path),
            "--output",
            str(tmp_path / "runs"),
        ]
    )
    captured = capsys.readouterr()
    assert captured.out == NARRATIVE
    metadata = json.loads(captured.err)
    assert metadata["completion_reason"] == reason
    assert code == (0 if reason == "complete" else 1)
    assert Path(metadata["artifacts"]["report"]).read_bytes() == NARRATIVE.encode()
    assert (
        json.loads(Path(metadata["artifacts"]["result"]).read_text())["run_id"]
        == metadata["run_id"]
    )


@pytest.mark.parametrize("stop", ["cancelled", "checkpoint", "limit_turns", "failed", "tool_use"])
async def test_noncompletion_stop_never_promotes_available_message(tmp_path, stop):
    # Catches selecting message/history text without checking the actual final stop reason.
    class Lead:
        async def invoke_async(self, question):
            return SimpleNamespace(
                stop_reason=stop, message={"role": "assistant", "content": [{"text": NARRATIVE}]}
            )

    result = await run_team(
        TeamRequest(question="Inspect", mode="education"),
        config=direct_config(tmp_path),
        deps=TeamDependencies(role_factory=lambda *_: Lead()),
    )
    assert result.completion_reason != "complete"
    assert NARRATIVE not in result.report_markdown


@pytest.mark.parametrize("step", [{"text": ""}, output("unknown", summary="tool arguments")])
async def test_empty_or_tool_only_lead_has_no_synthetic_success(tmp_path, step):
    # Catches extraction of a tool's arguments or old narrative after the real return is absent.
    result = await run_team(
        TeamRequest(question="Inspect", mode="education"),
        config=direct_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: ScriptModel(step)),
    )
    assert result.completion_reason == "failed"
    assert "tool arguments" not in result.report_markdown


async def test_lead_token_cutoff_preserves_only_actual_text_as_partial(tmp_path):
    # Catches promoting max-token cutoff to complete, or replacing its available prose.
    result = await run_team(
        TeamRequest(question="Inspect", mode="discovery"),
        config=direct_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: CutoffModel({"text": NARRATIVE})),
    )
    assert result.completion_reason == "partial"
    assert result.report_markdown == NARRATIVE
    assert Path(result.artifacts["report"]).read_bytes() == NARRATIVE.encode()


@pytest.mark.parametrize(
    "failure,want",
    [
        (RuntimeError("transport"), "failed"),
        (TimeoutError(), "deadline"),
        (asyncio.CancelledError(), "cancelled"),
    ],
)
async def test_lead_runtime_failures_remain_honest(tmp_path, failure, want):
    # Catches converting transport/deadline/cancel errors into a successful empty narrative.
    class Lead:
        async def invoke_async(self, question):
            raise failure

    result = await run_team(
        TeamRequest(question="Inspect", mode="education"),
        config=direct_config(tmp_path),
        deps=TeamDependencies(role_factory=lambda *_: Lead()),
    )
    assert result.completion_reason == want
    assert NARRATIVE not in result.report_markdown


async def test_direct_lead_reserve_suppresses_tools_and_accepts_plain_final_text(tmp_path):
    # Catches terminal middleware forcing TeamResult or permitting research in the reserve.
    executed = []

    async def research_batch(jobs: list[dict]) -> dict:
        """This must be suppressed inside the final reserve."""
        executed.append(jobs)
        return {"status": "success"}

    model = ScriptModel(output("research_batch", jobs=[]), {"text": NARRATIVE})
    ctx = context(tmp_path, "team_lead", model, {"research_batch": research_batch}, limit=4)
    ctx.config = direct_config(tmp_path).model_copy(
        update={
            "finalization": FinalizationConfig(
                expert_reserve_generations=4, analyst_reserve_generations=4
            )
        }
    )
    agent = build_role("team_lead", ctx)
    response = await agent.invoke_async("Inspect")
    assert response.stop_reason == "end_turn"
    assert response.message["content"] == [{"text": NARRATIVE}]
    assert not executed and ctx.terminal_only
    assert all(not call["tools"] for call in model.calls)
    assert all(call["tool_choice"] is None for call in model.calls)


async def test_typed_empty_narrative_is_failure_even_with_valid_metadata(tmp_path):
    # Catches accepting a schema-valid output object that contains no actual final answer.
    result = await run_team(
        TeamRequest(question="Inspect", mode="education"),
        config=config(tmp_path),
        deps=TeamDependencies(
            model_factory=lambda *_: ScriptModel(output("TeamResult", summary=""))
        ),
    )
    assert result.completion_reason == "failed"
