"""Direct child prose is transported by the real SDK without structured formatting."""

import json
from collections import deque
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_entrypoint import config
from portfolio_management.config import TeamConfig
from portfolio_management.team import run_team
from portfolio_management.types import TeamDependencies, TeamRequest


class CutoffModel(ScriptModel):
    async def stream(self, *args, **kwargs):
        async for event in super().stream(*args, **kwargs):
            if "messageStop" in event:
                event = {"messageStop": {"stopReason": "max_tokens"}}
            yield event


def direct_config(tmp_path):
    return TeamConfig.model_validate(config(tmp_path).model_dump(exclude={"child_output_mode"}))


def test_default_and_terminal_validation(tmp_path):
    assert (
        TeamConfig.model_validate(
            config(tmp_path).model_dump(exclude={"child_output_mode"})
        ).child_output_mode
        == "direct_text"
    )
    assert direct_config(tmp_path).child_output_mode == "direct_text"
    with pytest.raises(ValidationError):
        TeamConfig.model_validate(
            direct_config(tmp_path).model_dump()
            | {"finalization": {"analyst_terminal_seconds": 1.0}}
        )


@pytest.mark.parametrize("cutoff", [False, True])
async def test_sdk_text_exact_relay_and_cutoff(tmp_path, cutoff):
    narrative = "  ## Source α\nhttps://example.org/a?x=1&y=2 [evidence:raw-id]\nunfinished?  "
    lead = ScriptModel(
        output("research_batch", jobs=[{"job_id": "a", "question": "inspect"}]),
        output("consult_expert", question=narrative),
        {"text": "Offline transport only"},
    )
    analyst = (CutoffModel if cutoff else ScriptModel)({"text": narrative})
    expert = (CutoffModel if cutoff else ScriptModel)({"text": narrative})
    models = deque([lead, analyst, expert])
    result = await run_team(
        TeamRequest(question="test", mode="education"),
        config=direct_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    research = json.loads(Path(result.artifacts["research"]).read_text())[0]
    assert research["summary"] == narrative
    assert research["outcome"] == ("partial" if cutoff else "success")
    assert bool(research["error"]) is cutoff
    assert json.loads(expert.calls[0]["messages"][0]["content"][0]["text"])["question"] == "test"
    for child in (analyst, expert):
        assert len(child.calls) == 1
        assert not {"ResearchResult", "ExpertResult"} & {t["name"] for t in child.calls[0]["tools"]}
    delivered = json.loads(
        lead.calls[1]["messages"][-1]["content"][0]["toolResult"]["content"][0]["text"]
    )
    assert delivered[0]["summary"] == narrative
    review = json.loads(
        lead.calls[2]["messages"][-1]["content"][0]["toolResult"]["content"][0]["text"]
    )
    assert review["assessment"] == narrative
    assert review["outcome"] == ("partial" if cutoff else "success")


@pytest.mark.parametrize("failure", [RuntimeError("offline"), {"text": ""}])
async def test_no_body_or_error_is_failure(tmp_path, failure):
    lead = ScriptModel(
        output("research_batch", jobs=[{"job_id": "a", "question": "x"}]),
        {"text": "Failed child acknowledged"},
    )
    models = deque([lead, CutoffModel(failure)])
    result = await run_team(
        TeamRequest(question="test", mode="education"),
        config=direct_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    child = json.loads(Path(result.artifacts["research"]).read_text())[0]
    assert child["outcome"] == "failed"
    assert child["summary"] == ""


async def test_child_result_and_trace_io_do_not_gate_text(tmp_path, monkeypatch):
    import portfolio_management.team as team
    from portfolio_management.types import TraceSink

    injected = []
    original_write = team._write_json
    original_open = Path.open

    def write(path, value):
        if value and path.name in {"research-results.json", "expert-results.json"}:
            injected.append(path.name)
            raise OSError("disk")
        return original_write(path, value)

    def open_path(path, *args, **kwargs):
        if path.suffix == ".jsonl" and args and args[0] == "a":
            raise OSError("trace disk")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(team, "_write_json", write)
    monkeypatch.setattr(Path, "open", open_path)
    lead = ScriptModel(
        output("research_batch", jobs=[{"job_id": "a", "question": "x"}]),
        output("consult_expert", question="exact narrative"),
        {"text": "Delivered"},
    )
    models = deque(
        [lead, ScriptModel({"text": "exact narrative"}), ScriptModel({"text": "exact assessment"})]
    )
    result = await run_team(
        TeamRequest(question="test", mode="education"),
        config=direct_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert injected == ["research-results.json", "expert-results.json"]
    assert result.summary == "Delivered"
    assert "exact narrative" in json.dumps(lead.calls[1]["messages"])
    assert "exact assessment" in json.dumps(lead.calls[2]["messages"])
    sink = TraceSink(
        tmp_path / "broken.jsonl",
        lambda: __import__("datetime").datetime.now(__import__("datetime").UTC),
        best_effort_io=True,
    )
    sink.emit("test")
    assert sink.io_failures == 1


@pytest.mark.parametrize("reason", ["cancelled", "limit_turns", "checkpoint", "interrupt"])
async def test_noncompletion_return_cannot_promote_text(reason):
    from types import SimpleNamespace

    from portfolio_management.team import _invoke_child_text

    class Child:
        async def invoke_async(self, question):
            return SimpleNamespace(
                stop_reason=reason,
                message={"role": "assistant", "content": [{"text": "not completed"}]},
            )

    with pytest.raises(ValueError):
        await _invoke_child_text(Child(), "x")


async def test_calculator_validation_remains_in_direct_mode(tmp_path):
    lead = ScriptModel(
        output("research_batch", jobs=[{"job_id": "a", "question": "x"}]),
        {"text": "Validation retained"},
    )
    analyst = ScriptModel(
        output(
            "finance_calculate",
            operation="expiry_payoff",
            inputs={"SYN": "garbage"},
            refs={"positions": []},
        ),
        {"text": "Calculator input failed; no computed claim."},
    )
    models = deque([lead, analyst])
    result = await run_team(
        TeamRequest(question="test", mode="education"),
        config=direct_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert result.summary == "Validation retained"
    assert len(analyst.calls) == 2
    tool_result = analyst.calls[1]["messages"][-1]["content"][0]["toolResult"]
    assert tool_result["status"] == "error" or "invalid_data" in json.dumps(tool_result)


@pytest.mark.parametrize("error", [TimeoutError(), __import__("asyncio").CancelledError()])
async def test_timeout_cancellation_do_not_promote_history(error):
    from portfolio_management.team import _invoke_child_text

    class Child:
        messages = [{"role": "assistant", "content": [{"text": "earlier text"}]}]

        async def invoke_async(self, question):
            self.messages.append({"role": "assistant", "content": [{"text": "fragment"}]})
            raise error

    with pytest.raises(type(error)):
        await _invoke_child_text(Child(), "x")


async def test_cutoff_tool_arguments_and_reasoning_are_not_prose(tmp_path):
    from strands import Agent
    from strands.types.exceptions import MaxTokensReachedException

    from portfolio_management.team import _invoke_child_text

    model = CutoffModel(output("unknown", secret="arguments"))
    with pytest.raises(MaxTokensReachedException):
        await _invoke_child_text(Agent(model=model, callback_handler=None), "x")

    class NoCurrentBody:
        messages = [{"role": "assistant", "content": [{"text": "old body"}]}]

        async def invoke_async(self, question):
            raise MaxTokensReachedException("cutoff")

    with pytest.raises(MaxTokensReachedException):
        await _invoke_child_text(NoCurrentBody(), "x")


def test_cli_defaults_to_ordinary_children_and_lead_text(tmp_path, capsys):
    # Catches CLI config silently selecting typed children when no mode flag was supplied.
    from portfolio_management.cli import main

    path = tmp_path / "config.json"
    value = direct_config(tmp_path).model_dump(mode="json", exclude={"child_output_mode"})
    value["model"]["script"] = {
        "role_scripts": {
            "team_lead": [
                [
                    output("research_batch", jobs=[{"job_id": "chosen", "question": "Inspect"}]),
                    output("consult_expert", question="Review", job_ids=["chosen"]),
                    {"text": "Native CLI result"},
                ]
            ],
            "analyst": [[{"text": "  Original child [source:exact]  "}]],
            "wheel_expert": [[{"text": "  Original expert  "}]],
        }
    }
    path.write_text(json.dumps(value))
    assert (
        main(
            [
                "ask",
                "--question",
                "Inspect",
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
    captured = capsys.readouterr()
    assert captured.out == "Native CLI result"
    response = json.loads(captured.err)
    result = json.loads(Path(response["artifacts"]["result"]).read_text())
    assert result["summary"] == "Native CLI result"
    assert result["completion_reason"] == "complete"
    assert json.loads(Path(result["artifacts"]["research"]).read_text())[0]["summary"] == (
        "  Original child [source:exact]  "
    )
    assert json.loads(Path(result["artifacts"]["expert"]).read_text())[0]["assessment"] == (
        "  Original expert  "
    )
    manifest = json.loads(Path(result["artifacts"]["manifest"]).read_text())
    assert manifest["child_output_mode"] == "direct_text"
    assert "ResearchResult" not in manifest["tool_specs"]["analyst"]
    assert "ExpertResult" not in manifest["tool_specs"]["wheel_expert"]
    assert "TeamResult" not in manifest["tool_specs"]["team_lead"]
