"""Known private identities do not confer calculator/reference authority."""

from pathlib import Path

import pytest

from tests.support.thin_fakes import fixed_clock
from tests.thin.test_entrypoint import config, snapshot
from wheel_team.contracts import PortfolioSnapshot
from wheel_team.team import run_team
from wheel_team.types import ExpertResult, ResearchResult, TeamDependencies, TeamRequest, TeamResult
from wheel_team.vault import RecordNotFound


async def test_analyst_cannot_use_private_snapshot_history_or_calculation_refs(tmp_path):
    private_refs = []
    observed = {}

    class Role:
        def __init__(self, role, ctx):
            self.role, self.ctx = role, ctx

        async def invoke_async(self, question):
            ctx = self.ctx
            if self.role == "team_lead":
                history = PortfolioSnapshot.model_validate(snapshot()).model_copy(
                    update={"snapshot_id": "private-history"}
                )
                private_refs.append(ctx.store.put("snapshot", history.model_dump(mode="json")))
                reports = await ctx.tools["research_batch"](
                    [{"job_id": "public", "question": "Public research only"}]
                )
                assert reports[0]["outcome"] == "success"
                blocked = await ctx.tools["research_batch"](
                    [
                        {
                            "job_id": "blocked",
                            "question": "Public task",
                            "evidence_refs": private_refs,
                        }
                    ]
                )
                assert blocked[0]["outcome"] == "failed"
                expert = await ctx.tools["consult_expert"]()
                assert expert["error"] is None
                return TeamResult(summary="Delivered")
            if self.role == "analyst":
                calc = ctx.tools["finance_calculate"]
                current = await calc(
                    "analyze_portfolio", {}, {"snapshot_ref": "synthetic-snapshot"}
                )
                assert current["status"] == "invalid_data"
                history = await calc("performance", {}, {"history_refs": private_refs})
                assert history["status"] == "invalid_data"
                for ref in ["snapshot:synthetic-snapshot", *private_refs]:
                    with pytest.raises(RecordNotFound):
                        ctx.store.get(ref)
                    read = await ctx.tools["evidence_read"](ref)
                    assert read["status"] != "success"
                arithmetic = await calc(
                    "arithmetic", {"values": ["12", "3"]}, {"operation": "divide"}
                )
                assert arithmetic["status"] == "success"
                observed["public_calculation"] = arithmetic["result_refs"][0]
                return ResearchResult(
                    job_id="public", outcome="success", summary="Public arithmetic"
                )
            current = await ctx.tools["finance_calculate"](
                "analyze_portfolio", {}, {"snapshot_ref": "synthetic-snapshot"}
            )
            assert current["status"] != "invalid_data"
            assert (
                ctx.store.get("snapshot:synthetic-snapshot")["account_alias"] == "synthetic-account"
            )
            assert ctx.store.get(observed["public_calculation"])
            return ExpertResult(assessment="Private assessment")

    result = await run_team(
        TeamRequest(
            question="PRIVATE OWNER TASK",
            mode="combined",
            portfolio=snapshot(),
            owner_context="Private owner claim",
        ),
        config=config(tmp_path),
        deps=TeamDependencies(role_factory=lambda role, ctx: Role(role, ctx), clock=fixed_clock),
    )
    assert result.completion_reason == "complete"
    public_dir = Path(result.artifacts["result"]).parent / "records"
    assert public_dir.is_dir()
    public_text = "".join(p.read_text() for p in public_dir.glob("*.json"))
    assert "synthetic-account" not in public_text and "Private owner claim" not in public_text


async def test_scoped_readers_cannot_resolve_guessed_private_calculation(tmp_path):
    from wheel_team.privacy import PrivateEvidenceStore
    from wheel_team.tools.finance import finance_tools
    from wheel_team.vault import EvidenceStore

    public = EvidenceStore(tmp_path / "public")
    private = PrivateEvidenceStore(tmp_path / "private", public)
    receipt = await finance_tools(private, clock=fixed_clock)["finance_calculate"](
        "arithmetic", {"values": ["123456", "2"]}, {"operation": "multiply"}
    )
    ref = receipt["result_refs"][0]
    assert private.get(ref)
    with pytest.raises(RecordNotFound):
        public.get(ref)
    assert public.find("calculation", {}) == []
