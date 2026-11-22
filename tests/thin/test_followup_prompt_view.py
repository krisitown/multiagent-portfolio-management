"""Large parent audit inventories must not crowd out exact decision originals."""

import copy
import json

import pytest

from tests.thin.test_agents import context
from tests.thin.test_analyst_report_access import ContextModel
from tests.thin.test_portfolio_council import council_config
from portfolio_management.agents.base import build_role
from portfolio_management.prompt_loader import load_prompt

PRIOR_MARKER = "Prior review (private ordinary dated data; not current observations):\n"


def large_packet():
    original = "  Original α\r\nUnresolved judgment, not owner adoption.  " * 200
    return {
        "parent_run_id": "run-parent",
        "review_session_id": "run-original",
        "prior_question": "PRIVATE OWNER QUESTION",
        "prior_completed_at": "2026-11-11T00:45:53+00:00",
        "prior_completion_reason": "deadline",
        "prior_report": original,
        "final_author": "portfolio_manager",
        "analyst_reports": [
            {"job_id": "a", "outcome": "success", "summary": original},
            {"job_id": "b", "outcome": "cancelled", "summary": "", "error": "Cancelled"},
        ],
        "expert_reports": [{"assessment": original, "error": None}],
        "specialist_assessments": [
            {
                "role": "buffett",
                "outcome": "partial",
                "result": {"assessment": original, "error": "max_tokens"},
            }
        ],
        "explorer_reports": [{"job_id": "e", "outcome": "success", "summary": original}],
        "asymmetric_reports": [{"job_id": "x", "outcome": "failed", "summary": ""}],
        "portfolio_revision": {"snapshot_id": "dated-private", "as_of": "2026-11-09"},
        "integrity_scope": "checkpoint_verified",
        "freshness": "Dated observations, not refreshed by inheritance.",
        # Future decision metadata must not disappear through a fixed-field whitelist.
        "future_decision_metadata": {"source_dates": ["A report-local date must survive"]},
        "artifact_hashes": {f"records/{n:064x}.json": "a" * 64 for n in range(1500)},
        "record_hashes": {
            f"evidence:{n:064x}": {"sha256": "b" * 64, "scope": "public"} for n in range(1500)
        },
        "source_dates": [
            {"record_ref": f"evidence:{n:064x}", "source_timestamp": "2026-11-09T21:00:00Z"}
            for n in range(1500)
        ],
    }


@pytest.mark.parametrize(
    "role", ["team_lead", "portfolio_manager", "wheel_expert", "dalio", "buffett"]
)
async def test_sdk_private_prompt_preserves_originals_without_unbounded_audit_ledgers(
    tmp_path, role
):
    # Reintroducing full-packet serialization, dropping report metadata, recursively
    # stripping dates, or mutating the audit packet must all fail this boundary test.
    packet = large_packet()
    before = copy.deepcopy(packet)
    model = ContextModel({"text": "Offline result"})
    ctx = context(tmp_path, "team_lead", model)
    ctx.config = council_config(tmp_path)
    ctx.prompt = load_prompt(role, ctx.config.prompts)
    ctx.prior_review = packet
    await build_role(role, ctx).invoke_async("Continue")

    projected = json.loads(
        model.calls[0]["messages"][0]["content"][0]["text"].split(PRIOR_MARKER, 1)[1]
    )
    for field in ("artifact_hashes", "record_hashes", "source_dates"):
        assert field not in projected
    for field, value in before.items():
        if field not in {"artifact_hashes", "record_hashes", "source_dates"}:
            assert projected[field] == value
    assert len(model.system_prompt) < len(json.dumps(packet)) / 4
    assert packet == before


@pytest.mark.parametrize("role", ["analyst", "explorer"])
async def test_sdk_public_prompt_excludes_even_projected_private_parent(tmp_path, role):
    model = ContextModel({"text": "Public result"})
    ctx = context(tmp_path, role, model)
    ctx.prior_review = large_packet()
    await build_role(role, ctx).invoke_async("Public question")
    assert PRIOR_MARKER not in model.system_prompt
    assert "PRIVATE OWNER QUESTION" not in model.system_prompt
    assert "Original α" not in model.system_prompt


async def test_sdk_report_only_parent_retains_scope_without_claiming_imported_sources(tmp_path):
    packet = {
        "context_scope": "report_only",
        "parent_run_id": "run-parent",
        "prior_report": "  Original α\r\nDated report only  ",
        "report_sha256": "a" * 64,
        "freshness": "Historical evidence was not imported.",
    }
    model = ContextModel({"text": "Explanation"})
    ctx = context(tmp_path, "team_lead", model)
    ctx.prior_review = packet
    await build_role("team_lead", ctx).invoke_async("Explain")
    assert (
        json.loads(model.calls[0]["messages"][0]["content"][0]["text"].split(PRIOR_MARKER, 1)[1])
        == packet
    )
