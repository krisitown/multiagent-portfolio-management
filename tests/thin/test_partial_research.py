"""Stopped foreground runs keep completed child outputs without promoting failed work."""

import asyncio
import json
from collections import deque
from pathlib import Path

from tests.support.thin_fakes import fixed_clock
from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_entrypoint import config
from wheel_team.contracts import EvidenceRecord, MarketResult
from wheel_team.team import run_team
from wheel_team.types import TeamDependencies, TeamRequest


async def test_sdk_deadline_keeps_completed_analyst_summary_and_actual_evidence_refs(tmp_path):
    summary = "Isolated completed analyst source summary; no issuer observations or final judgment."
    reference = "evidence:partial-source"

    class Source:
        def capabilities(self):
            return ["test_evidence"]

        async def query(self, request):
            return MarketResult(
                query_id=request.query_id,
                status="success",
                complete=False,
                evidence=(
                    EvidenceRecord(
                        evidence_id="partial-source",
                        source="isolated-test-source",
                        fetched_at=fixed_clock(),
                        delay_state="unknown",
                        content={"notice": "Isolated preservation test; no issuer observations"},
                        source_url="https://example.org/partial-source",
                    ),
                ),
            )

    lead = ScriptModel(
        output(
            "research_batch",
            jobs=[{"job_id": "finished", "question": "Inspect supplied test source"}],
        ),
        asyncio.Event(),
    )
    analyst = ScriptModel(
        output(
            "market_query",
            query={"query_id": "partial-source-query", "capability": "test_evidence"},
        ),
        output(
            "ResearchResult",
            job_id="finished",
            outcome="success",
            summary=summary,
            evidence_refs=[reference],
        ),
    )
    models = deque([lead, analyst])
    result = await run_team(
        TeamRequest(question="Inspect partial output preservation", mode="focused"),
        config=config(tmp_path).model_copy(update={"deadline_seconds": 0.2}),
        deps=TeamDependencies(
            model_factory=lambda *_: models.popleft(), market_provider=Source(), clock=fixed_clock
        ),
    )
    assert result.completion_reason == "deadline"
    assert summary in result.summary
    assert "Partial analyst research" in result.summary
    assert "Run deadline reached before final synthesis." in result.summary
    assert reference in result.supporting_refs
    assert result.supporting_records[reference]["source"] == "isolated-test-source"
    assert result.warnings == ("Run deadline reached; available partial artifacts were preserved.",)
    saved = json.loads(Path(result.artifacts["result"]).read_text())
    assert saved["completion_reason"] == "deadline"
    assert summary in saved["summary"]
    assert reference in saved["supporting_refs"]
    report = Path(result.artifacts["report"]).read_text()
    assert summary in report
    assert "Partial analyst research" in report
    assert "isolated-test-source" in report
    assert "[Original source](https://example.org/partial-source)" in report


def test_partial_helper_skips_failed_children_and_keeps_only_explicit_completed_refs():
    import wheel_team.team as team
    from wheel_team.types import ExpertResult, ResearchResult, TeamResult

    stopped = TeamResult(
        completion_reason="deadline",
        summary="Original stop summary",
        warnings=("Original warning",),
    )
    children = [
        ResearchResult(
            job_id="good",
            outcome="partial",
            summary="Completed partial child summary",
            evidence_refs=("evidence:declared",),
            idea_refs=("idea:declared:1",),
        ),
        ResearchResult(
            job_id="failed",
            outcome="failed",
            summary="FAILED CHILD MUST NOT APPEAR",
            evidence_refs=("evidence:failed",),
            error="Failure",
        ),
    ]
    experts = [
        ExpertResult(assessment="Completed expert assessment", shortlist_refs=("idea:declared:1",)),
        ExpertResult(assessment="FAILED EXPERT MUST NOT APPEAR", error="Failure"),
    ]
    retained = team.preserve_partial_research(stopped, children, experts)
    assert retained.completion_reason == "deadline"
    assert retained.warnings == ("Original warning",)
    assert retained.supporting_refs == ("evidence:declared", "idea:declared:1")
    assert "Completed partial child summary" in retained.summary
    assert "Completed expert assessment" in retained.summary
    assert "FAILED CHILD" not in retained.summary
    assert "FAILED EXPERT" not in retained.summary
    assert retained.candidate_slots == ()
    complete = stopped.model_copy(update={"completion_reason": "complete"})
    assert team.preserve_partial_research(complete, children, experts) is complete
