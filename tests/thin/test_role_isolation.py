"""Real SDK loop, injected transport, and no real role tools or side effects."""

import json
import socket

import pytest

from tests.support.thin_fakes import fixed_clock, recording_factory
from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_entrypoint import config
from wheel_team.config import ModelConfig
from wheel_team.evaluation.thin_runner import run_case
from wheel_team.types import TeamDependencies


def model_config(tmp_path):
    return config(tmp_path).model_copy(
        update={
            "model": ModelConfig(
                provider="openai",
                model_id="offline-transport",
                endpoint="http://unused.invalid/v1",
                api_key_env="UNUSED_TEST_KEY",
                allow_inference_network=False,
            )
        }
    )


@pytest.mark.parametrize(
    "role,tools,final",
    [
        (
            "team_lead",
            ["explore", "research_batch", "consult_expert"],
            output("TeamResult", summary="Controlled lead answer"),
        ),
        (
            "analyst",
            [
                "finance_calculate",
                "knowledge_read",
                "market_query",
                "public_search",
                "fetch_sources",
                "evidence_read",
                "evidence_find",
            ],
            output(
                "ResearchResult", job_id="isolated", outcome="success", summary="Controlled analyst"
            ),
        ),
        (
            "explorer",
            ["public_search", "market_statistics", "options_liquidity"],
            output(
                "ResearchResult",
                job_id="isolated",
                outcome="success",
                summary="Controlled explorer",
            ),
        ),
        (
            "wheel_expert",
            [
                "finance_calculate",
                "knowledge_read",
                "evidence_read",
                "evidence_find",
                "request_research",
            ],
            output("ExpertResult", assessment="Controlled expert answer"),
        ),
    ],
)
async def test_role_mode_mocks_every_exposed_tool_and_uses_actual_sdk(
    tmp_path, monkeypatch, role, tools, final
):
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("network forbidden"))
    scripts = [
        output(
            name,
            **{
                "finance_calculate": {"formula": "put", "inputs": {}},
                "knowledge_read": {"module_id": "fake"},
                "public_search": {"query": "test"},
                "fetch_sources": {"urls": ["https://example.org"]},
                "research_batch": {"jobs": []},
                "request_research": {"jobs": []},
                "evidence_read": {"record_ref": "evidence:controlled"},
                "evidence_find": {"kind": "evidence"},
                "explore": {"question": "Controlled market context"},
                "consult_expert": {},
                "market_query": {"query": {}},
                "market_statistics": {"series": []},
                "options_liquidity": {"symbol": "AAPL"},
            }[name],
        )
        for name in tools
    ]
    model = ScriptModel(*scripts, final)
    summary = await run_case(
        {
            "case_id": "isolation",
            "role": role,
            "question": "Controlled question",
            "mode": "education",
            "mocked_tools": {name: [{"controlled": name}] for name in tools},
        },
        model_config(tmp_path),
        TeamDependencies(clock=fixed_clock, model_factory=lambda *a: model),
        mode="role",
    )
    assert summary["evaluation_mode"] == "role"
    assert summary["role"] == role
    assert summary["inference_kind"] == "injected_sdk_transport"
    assert summary["live_acceptance"] is False
    assert summary["metrics"]["model_calls"] == len(tools) + 1
    assert summary["metrics"]["tool_calls"] == len(tools)
    assert len([row for row in summary["trace"] if row["event"] == "mock_tool_result"]) == len(
        tools
    )
    assert all(
        row["result"]["controlled"] == row["name"]
        for row in summary["trace"]
        if row["event"] == "mock_tool_result"
    )
    assert not list((tmp_path / summary["run_id"] / "records").glob("*.json"))


async def test_role_mode_rejects_custom_role_factory_and_live_tool_overrides(tmp_path):
    case = {"case_id": "isolation", "question": "Question", "role": "team_lead"}
    with pytest.raises(ValueError, match="role_factory"):
        await run_case(
            case,
            model_config(tmp_path),
            TeamDependencies(role_factory=recording_factory),
            mode="role",
        )
    with pytest.raises(ValueError, match="tool_overrides"):
        await run_case(
            case,
            model_config(tmp_path),
            TeamDependencies(tool_overrides={"team_lead": {"evidence_read": lambda: "unsafe"}}),
            mode="role",
        )
    assert not list(tmp_path.iterdir())


async def test_recoverable_mock_failure_can_still_produce_successful_final_output(tmp_path):
    model = ScriptModel(
        output("market_query", query={}),
        output(
            "ResearchResult",
            job_id="recover",
            outcome="success",
            summary="Sources unavailable; uncertainty preserved",
        ),
    )
    summary = await run_case(
        {
            "case_id": "recover",
            "role": "analyst",
            "question": "Research",
            "mode": "focused",
            "mocked_tools": {"market_query": [{"raise": "source_unavailable"}]},
        },
        model_config(tmp_path),
        TeamDependencies(clock=fixed_clock, model_factory=lambda *a: model),
        mode="role",
    )
    assert summary["completion_reason"] == "complete"
    assert "uncertainty preserved" in summary["final_response"]
    assert "tool_failure" in {finding["code"] for finding in summary["findings"]}
    assert json.loads(open(summary["artifacts"]["manifest"]).read())["tools_mocked"] == "all"


async def test_argument_keyed_role_mocks_allow_defensible_research_orders(tmp_path):
    model = ScriptModel(
        output("knowledge_read", module_id="later"),
        output("knowledge_read", module_id="earlier"),
        output("ResearchResult", job_id="order", outcome="success", summary="Compared"),
    )
    case = {
        "case_id": "flexible-order",
        "role": "analyst",
        "question": "Compare sources",
        "mocked_tools": {
            "knowledge_read": {
                "by_arguments": [
                    {"arguments": {"module_id": "earlier"}, "response": {"source": "early"}},
                    {"arguments": {"module_id": "later"}, "response": {"source": "late"}},
                ]
            }
        },
    }
    summary = await run_case(
        case,
        model_config(tmp_path),
        TeamDependencies(model_factory=lambda *a: model, clock=fixed_clock),
        mode="role",
    )
    assert summary["completion_reason"] == "complete"
    returned = [row["result"] for row in summary["trace"] if row["event"] == "mock_tool_result"]
    assert returned == [{"source": "late"}, {"source": "early"}]


async def test_isolated_role_records_exact_selected_knowledge_hash(tmp_path):
    from pathlib import Path

    model = ScriptModel(
        output("ResearchResult", job_id="knowledge", outcome="success", summary="Done")
    )
    cfg = model_config(tmp_path).model_copy(
        update={
            "knowledge_manifest_path": Path("tests/fixtures/knowledge/valid_minimal/manifest.json")
        }
    )
    summary = await run_case(
        {"case_id": "knowledge", "role": "analyst", "question": "Review"},
        cfg,
        TeamDependencies(model_factory=lambda *a: model, clock=fixed_clock),
        mode="role",
    )
    assert summary["knowledge_hashes"]["synthetic-arithmetic"] == (
        "55b91af18c0b605764862bb7b9dd02a5a90a3e68c3f16399c846bf15688ed377"
    )


async def test_role_mode_cannot_select_development_knowledge_for_production(tmp_path):
    import hashlib
    from pathlib import Path

    from wheel_team.config import PromptSet

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    text = b"Controlled test role fixture."
    (bundle / "role.md").write_bytes(text)
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "prompt_set_id": "controlled",
                "version": "1",
                "status": "approved",
                "owner_approval_ref": "synthetic-owner-fixture",
                "roles": {
                    "analyst": {"path": "role.md", "content_hash": hashlib.sha256(text).hexdigest()}
                },
            }
        )
    )
    cfg = model_config(tmp_path / "runs").model_copy(
        update={
            "prompts": PromptSet(manifest_path=bundle / "manifest.json"),
            "knowledge_manifest_path": Path("tests/fixtures/knowledge/valid_minimal/manifest.json"),
        }
    )
    with pytest.raises(ValueError, match="Development knowledge"):
        await run_case(
            {"case_id": "knowledge", "role": "analyst", "question": "Review"},
            cfg,
            TeamDependencies(model_factory=lambda *a: ScriptModel()),
            mode="role",
        )
    assert not (tmp_path / "runs").exists()


async def test_lead_mock_consultation_matches_no_argument_holistic_contract(tmp_path):
    # Catches evaluation advertising selectors absent from the current real host tool.
    model = ScriptModel(
        output("consult_expert"),
        output("TeamResult", summary="Selection checked"),
    )
    summary = await run_case(
        {
            "case_id": "select-jobs",
            "role": "team_lead",
            "question": "Review",
            "mocked_tools": {
                "consult_expert": [
                    {
                        "arguments": {},
                        "response": {"assessment": "Original selected report reviewed"},
                    }
                ]
            },
        },
        model_config(tmp_path),
        TeamDependencies(model_factory=lambda *_: model, clock=fixed_clock),
        mode="role",
    )
    delivered = model.calls[1]["messages"][-1]["content"][0]["toolResult"]
    assert delivered["status"] == "success"
    assert json.loads(delivered["content"][0]["text"])["assessment"] == (
        "Original selected report reviewed"
    )
    assert summary["completion_reason"] == "complete"


async def test_lead_mock_exploration_preserves_narrative(tmp_path):
    model = ScriptModel(
        output("explore", question="Broad context"),
        output("TeamResult", summary="Exploration reviewed"),
    )
    narrative = "Synthetic market context with an explicit data gap."
    summary = await run_case(
        {
            "case_id": "explore-narrative",
            "role": "team_lead",
            "question": "Review",
            "mocked_tools": {"explore": [narrative]},
        },
        model_config(tmp_path),
        TeamDependencies(model_factory=lambda *_: model, clock=fixed_clock),
        mode="role",
    )
    rows = [row for row in summary["trace"] if row["event"] == "mock_tool_result"]
    assert rows[0]["name"] == "explore"
    assert rows[0]["result"] == narrative
    assert narrative in json.dumps(model.calls[-1]["messages"])
