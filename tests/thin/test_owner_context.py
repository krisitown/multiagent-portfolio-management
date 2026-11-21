"""Private owner data stays separate from reusable instructions/public assignments."""

import json
from collections import deque
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_agents import context
from tests.thin.test_direct_text_handoff import direct_config
from wheel_team.agents.base import build_role
from wheel_team.contracts import AgentRole
from wheel_team.prompt_loader import load_prompt
from wheel_team.team import run_team
from wheel_team.types import TeamDependencies, TeamRequest

PRIVATE = "  Owner statement, not verified: private preference α\r\nNot a broker limit.\n"


@pytest.mark.parametrize("role", ["team_lead", "wheel_expert", "analyst", "explorer"])
def test_only_private_roles_receive_original_owner_context_once(tmp_path, role):
    ctx = context(tmp_path, "analyst" if role == "explorer" else role, ScriptModel())
    ctx.prompt = load_prompt(AgentRole(role), ctx.config.prompts)
    ctx.request = TeamRequest(question="PRIVATE QUESTION", mode="combined", owner_context=PRIVATE)
    agent = build_role(role, ctx)
    assert PRIVATE not in agent.system_prompt
    supplied = "".join(b.get("text", "") for m in agent.messages for b in m["content"])
    assert supplied.count(PRIVATE) == (1 if role in {"team_lead", "wheel_expert"} else 0)
    assert PRIVATE not in ctx.prompt.content


async def test_public_role_factory_never_receives_private_question_or_owner_context(tmp_path):
    models = deque(
        [
            ScriptModel(
                output(
                    "research_batch", jobs=[{"job_id": "j", "question": "Public instrument facts"}]
                ),
                output("consult_expert"),
                {"text": "Delivered"},
            ),
            ScriptModel({"text": "Public facts"}),
            ScriptModel({"text": "Assessment"}),
        ]
    )
    seen = []

    def factory(role, ctx):
        seen.append((role, ctx.request))
        ctx.model = models.popleft()
        return build_role(role, ctx)

    result = await run_team(
        TeamRequest(question="PRIVATE QUESTION", mode="combined", owner_context=PRIVATE),
        config=direct_config(tmp_path),
        deps=TeamDependencies(role_factory=factory),
    )
    assert result.completion_reason == "complete"
    public = next(r for role, r in seen if role == "analyst")
    assert public.owner_context is None
    assert public.question != "PRIVATE QUESTION"
    assert next(r for role, r in seen if role == "wheel_expert").owner_context == PRIVATE
    assert json.loads(open(result.artifacts["request"]).read())["owner_context"] == PRIVATE


def test_followup_request_requires_explicit_parent_and_kind():
    for fields in [{"review_kind": "explanation"}, {"parent_review": "/prior"}]:
        with pytest.raises(ValidationError):
            TeamRequest(question="Why?", mode="education", **fields)
    r = TeamRequest(
        question="Why?", mode="education", parent_review="/prior", review_kind="explanation"
    )
    assert r.review_kind == "explanation"


def test_cli_reads_owner_context_as_exact_separate_utf8(tmp_path, monkeypatch):
    from wheel_team import cli

    path = tmp_path / "owner.txt"
    path.write_bytes(PRIVATE.encode())
    seen = []

    async def run(request, **kwargs):
        seen.append(request)
        return SimpleNamespace(
            report_markdown="Original", run_id="offline", completion_reason="complete", artifacts={}
        )

    monkeypatch.setattr(cli, "load_team_config", lambda _: direct_config(tmp_path))
    monkeypatch.setattr(cli, "run_team", run)
    assert (
        cli.main(
            [
                "ask",
                "--question",
                "Review",
                "--mode",
                "combined",
                "--config",
                "unused",
                "--output",
                str(tmp_path),
                "--owner-context",
                str(path),
            ]
        )
        == 0
    )
    assert seen[0].owner_context == PRIVATE
    assert seen[0].question == "Review"
