"""Manager-first boundaries exercised through offline real SDK agents."""

import asyncio
import json
from collections import deque
from pathlib import Path

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_analyst_report_access import expert_data, tool_data
from tests.thin.test_portfolio_council import council_config
from portfolio_management.agents.base import build_role
from portfolio_management.team import run_team
from portfolio_management.types import TeamDependencies, TeamRequest


async def test_manager_starts_before_required_discovery_and_public_context_is_scoped(tmp_path):
    manager = ScriptModel(output("explore", question="PUBLIC alternatives"), {"text": "PM final"})
    explorer = ScriptModel({"text": "Public discovery with limitations"})
    models = deque([manager, explorer])
    contexts = []

    def factory(role, context):
        contexts.append((role, context))
        return build_role(role, context)

    result = await run_team(
        TeamRequest(
            question="PRIVATE TASK",
            mode="combined",
            portfolio_text="PRIVATE ACCOUNT",
            owner_context="PRIVATE OWNER",
        ),
        config=council_config(
            tmp_path,
            explorer_enabled=True,
            require_fresh_opportunities=True,
            max_expert_consultations=1,
            asymmetric_enabled=True,
        ),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft(), role_factory=factory),
    )
    assert [role for role, _ in contexts] == ["portfolio_manager", "explorer"]
    tools = {row["name"] for row in manager.calls[0]["tools"]}
    assert {
        "explore",
        "consult_asymmetric",
        "request_research",
        "consult_specialists",
    } <= tools
    assert "consult_portfolio_manager" not in tools
    assert expert_data(manager) == {"question": "PRIVATE TASK"}
    public = contexts[1][1].request.model_dump()
    assert "PRIVATE" not in json.dumps(public)
    assert "PRIVATE" not in json.dumps(explorer.calls)
    assert result.report_markdown == "PM final"


async def test_typed_audit_inventory_omitted_but_selected_original_is_exact(tmp_path):
    raw_refs = [f"evidence:raw-{n}" for n in range(300)]
    final = "Authored finding [evidence:raw-0]; dated and conditional."
    manager = ScriptModel(
        output("request_research", jobs=[{"job_id": "large", "question": "PUBLIC facts"}]),
        output("evidence_read", record_ref="input:analyst:1"),
        output("consult_specialists", requests=[{"role": "dalio", "question": "Independent view"}]),
        output("ExpertResult", assessment="PM final"),
    )
    analyst = ScriptModel(
        output(
            "ResearchResult",
            job_id="large",
            outcome="partial",
            summary=final,
            evidence_refs=raw_refs,
            error="One decisive source unavailable",
        )
    )
    peer = ScriptModel(output("ExpertResult", assessment="Peer final"))
    models = deque([manager, analyst, peer])
    result = await run_team(
        TeamRequest(question="PRIVATE task", mode="combined"),
        config=council_config(tmp_path, child_output_mode="typed"),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    view = tool_data(manager.calls[1])["reports"][0]
    assert view == {
        "job_id": "large",
        "author_role": "analyst",
        "evidence_ref": "input:analyst:1",
        "outcome": "partial",
        "summary": final,
        "error": "One decisive source unavailable",
    }
    original = json.loads(Path(result.artifacts["research"]).read_text())[0]
    assert original["evidence_refs"] == raw_refs
    assert tool_data(manager.calls[2])["record"] == original
    peer_input = expert_data(peer)
    assert peer_input["selected_reports"] == {}
    assert "raw-299" not in json.dumps(peer.calls)
    assert len(json.dumps(view)) < len(json.dumps(original)) / 10


async def test_independent_peer_calls_overlap_and_failures_keep_identity(tmp_path):
    both_started = asyncio.Event()
    started = []
    contexts = {}

    class Peer:
        def __init__(self, role):
            self.role = role

        async def invoke_async(self, question):
            started.append(self.role)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)
            if self.role == "buffett":
                raise RuntimeError("Unavailable")
            return {"assessment": "Dalio authored final"}

    manager = ScriptModel(
        output(
            "consult_specialists",
            requests=[
                {"role": "dalio", "question": "Macro"},
                {"role": "buffett", "question": "Business"},
            ],
        ),
        output("ExpertResult", assessment="PM final with business limitation"),
    )

    def factory(role, context):
        contexts[role] = context
        return build_role(role, context) if role == "portfolio_manager" else Peer(role)

    result = await run_team(
        TeamRequest(question="Task", mode="combined"),
        config=council_config(tmp_path, child_output_mode="typed"),
        deps=TeamDependencies(model_factory=lambda *_: manager, role_factory=factory),
    )
    assert started == ["dalio", "buffett"]
    handoff = tool_data(manager.calls[1])
    assert handoff["status"] == "partial"
    assert [(r["role"], r["outcome"]) for r in handoff["reports"]] == [
        ("dalio", "success"),
        ("buffett", "failed"),
    ]
    assert [r["evidence_ref"] for r in handoff["reports"]] == [
        "input:specialist:1",
        "input:specialist:2",
    ]
    assert handoff["reports"][1]["error"]
    saved = json.loads(Path(result.artifacts["specialists"]).read_text())
    assert saved[1]["role"] == "buffett" and saved[1]["outcome"] == "failed"
    unauthorized = await contexts["dalio"].tools["evidence_read"]("input:specialist:2")
    assert unauthorized["status"] == "unavailable"


async def test_peers_receive_assignment_then_select_exact_private_originals(tmp_path):
    from tests.thin.test_analyst_report_access import ContextModel

    private = "EXACT PRIVATE ACCOUNT: signed option -2, basis 12, expected funding unsettled"
    owner = "EXACT OWNER: property remains unsold"
    manager = ContextModel(
        output(
            "consult_specialists",
            requests=[{"role": "wheel_expert", "question": "Assess income alternatives"}],
        ),
        {"text": "PM final"},
    )
    peer = ContextModel(
        output("evidence_read", record_ref="input:portfolio"),
        output("evidence_read", record_ref="input:owner_context"),
        {"text": "Conditional peer view"},
    )
    models = deque([manager, peer])
    result = await run_team(
        TeamRequest(
            question="PRIVATE OWNER QUESTION",
            mode="combined",
            portfolio_text=private,
            owner_context=owner,
        ),
        config=council_config(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    assert private not in manager.system_prompt and owner not in manager.system_prompt
    assert private in json.dumps(manager.calls[0]) and owner in json.dumps(manager.calls[0])
    assert private not in peer.system_prompt and owner not in peer.system_prompt
    assert "PRIVATE OWNER QUESTION" not in json.dumps(peer.calls[0])
    assert tool_data(peer.calls[1])["record"] == private
    assert tool_data(peer.calls[2])["record"] == owner
    assert result.report_markdown == "PM final"


async def test_selected_oversized_explorer_original_is_labeled_bounded_excerpt(tmp_path):
    final = "Explorer source finding with a qualification. " * 10000
    manager = ScriptModel(
        output("explore", question="PUBLIC alternatives"),
        output("evidence_read", record_ref="input:explorer:1"),
        {"text": "PM final"},
    )
    models = deque([manager, ScriptModel({"text": final})])
    result = await run_team(
        TeamRequest(question="Review", mode="combined"),
        config=council_config(tmp_path, explorer_enabled=True, max_expert_consultations=1),
        deps=TeamDependencies(model_factory=lambda *_: models.popleft()),
    )
    read = tool_data(manager.calls[2])
    assert len(json.dumps(read)) < 20000
    assert read["record"]["tool_response_projection"]["excerpt_only"] is True
    assert read["record"]["tool_response_projection"]["omitted_chars"] > 0
    original = json.loads(Path(result.artifacts["explorer"]).read_text())
    assert final in json.dumps(original)
