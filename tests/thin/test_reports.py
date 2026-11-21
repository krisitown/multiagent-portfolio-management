"""Supported final artifacts preserve source semantics rather than fluent readiness."""

import importlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tests.support.thin_fakes import fixed_clock
from wheel_team.config import ModelConfig, PromptSet, TeamConfig
from wheel_team.contracts import CandidateStatus, ContractIdentity, IdeaRevision
from wheel_team.domain.option_math import put_economics, roll_economics
from wheel_team.team import run_team
from wheel_team.types import TeamDependencies, TeamRequest, TeamResult
from wheel_team.vault import EvidenceStore

from .test_tools import option, snapshot
from .test_vault import evidence


def api():
    try:
        return importlib.import_module("wheel_team.checks")
    except ModuleNotFoundError:
        pytest.fail("thin report validation/rendering is missing")


def unavailable():
    return tuple(
        {"slot": i, "kind": "unavailable", "reason": "Source outage; no evidence"}
        for i in range(1, 6)
    )


def candidate(store, company="SYN", status=CandidateStatus.CONDITION, **changes):
    quote = store.put("evidence", evidence("quote-" + company))
    quality = store.put(
        "quality",
        {
            "assessment_id": "quality-" + company,
            "company_id": company,
            "assessed_at": fixed_clock(),
            "conclusion": "qualified",
            "rationale": "Synthetic supported quality",
            "evidence_refs": [quote],
        },
    )
    calculation = put_economics(
        ContractIdentity.model_validate(option()),
        1,
        Decimal("2"),
        Decimal("3"),
        price_basis="premium",
    )
    calc = store.put("calculation", calculation.model_dump(mode="json"))
    idea = IdeaRevision(
        idea_id="idea-" + company,
        revision_id="r1",
        company_id=company,
        contract=option(),
        evidence_refs=(quote,),
        calculation_refs=(calc,),
        quality_ref=quality,
        thesis="Synthetic ownership thesis",
        countercase="Synthetic contrary case",
        status=status,
        reasons=("Wait for a current quote",),
        author="synthetic-analyst",
        task_id="test",
        created_at=fixed_clock(),
        **changes,
    )
    return store.put("idea", idea.model_dump(mode="json"))


def result_with_slots(slots, **changes):
    return TeamResult(
        mode="discovery", summary="Synthetic result", candidate_slots=slots, **changes
    )


def test_discovery_requires_five_numbered_distinct_company_slots(tmp_path):
    store = EvidenceStore(tmp_path)
    request = TeamRequest(question="Discover", mode="discovery")
    assert "candidate_slots_missing" in {
        f.code for f in api().validate_result(result_with_slots(()), store, request)
    }
    ref = candidate(store)
    duplicate = tuple({"slot": i, "kind": "candidate", "idea_ref": ref} for i in range(1, 6))
    findings = api().validate_result(result_with_slots(duplicate), store, request)
    assert "duplicate_company" in {f.code for f in findings}
    assert any(f.severity == "error" for f in findings)


@pytest.mark.parametrize("mode", ["focused", "education", "portfolio"])
def test_focused_education_and_portfolio_do_not_require_five_slots(tmp_path, mode):
    result = TeamResult(mode=mode, summary="Explanation")
    assert "candidate_slots_missing" not in {
        f.code
        for f in api().validate_result(
            result, EvidenceStore(tmp_path), TeamRequest(question="Explain", mode=mode)
        )
    }


def test_supported_condition_and_cold_outage_are_truthful(tmp_path):
    store = EvidenceStore(tmp_path)
    ref = candidate(store)
    slots = ({"slot": 1, "kind": "candidate", "idea_ref": ref},) + unavailable()[1:]
    request = TeamRequest(question="Discover", mode="discovery")
    findings = api().validate_result(result_with_slots(slots), store, request)
    assert not [f for f in findings if f.severity == "error"]
    assert "evidence_not_current" in {f.code for f in findings}
    assert not [
        f
        for f in api().validate_result(result_with_slots(unavailable()), store, request)
        if f.severity == "error"
    ]
    bad = result_with_slots(({"slot": 1, "kind": "unavailable"},) + unavailable()[1:])
    assert "invalid_candidate_slot" in {f.code for f in api().validate_result(bad, store, request)}


def test_unresolved_refs_and_unsupported_ready_are_blocked(tmp_path):
    store = EvidenceStore(tmp_path)
    ref = candidate(store, status=CandidateStatus.READY)
    slots = ({"slot": 1, "kind": "candidate", "idea_ref": ref},) + unavailable()[1:]
    findings = api().validate_result(
        result_with_slots(slots), store, TeamRequest(question="Discover", mode="discovery")
    )
    assert {"readiness_unverified", "evidence_not_current"} <= {f.code for f in findings}
    assert any(f.severity == "error" for f in findings if f.code == "readiness_unverified")
    missing = result_with_slots(
        ({"slot": 1, "kind": "candidate", "idea_ref": "idea:no@r1"},) + unavailable()[1:]
    )
    assert "unresolved_reference" in {
        f.code
        for f in api().validate_result(
            missing, store, TeamRequest(question="Discover", mode="discovery")
        )
    }


@pytest.mark.parametrize("formula", ["put", "roll"])
async def test_recalculates_persisted_outputs_including_roll_and_unknown_margin(tmp_path, formula):
    store = EvidenceStore(tmp_path)
    record = (
        put_economics(
            ContractIdentity.model_validate(option(multiplier="10")), 2, Decimal("2"), Decimal("3")
        )
        if formula == "put"
        else roll_economics(
            2, Decimal("10"), Decimal("2"), Decimal("5"), Decimal("6"), Decimal("4"), currency="USD"
        )
    )
    payload = record.model_dump(mode="json")
    payload["outputs"][next(iter(payload["outputs"]))] = "999999"
    payload["availability"]["broker_margin"] = "known"
    payload["outputs"]["broker_margin"] = "0"
    ref = store.put("calculation", payload)
    result = TeamResult(summary="Check", mode="focused", supporting_refs=(ref,))
    findings = api().validate_result(result, store, TeamRequest(question="Check", mode="focused"))
    assert "calculation_mismatch" in {f.code for f in findings}


async def test_renderer_preserves_eod_warnings_exact_economics_and_all_holdings(tmp_path):
    class Lead:
        def __init__(self, context):
            self.context = context

        async def invoke_async(self, question):
            store = self.context.store
            ref = candidate(store)
            roll = roll_economics(
                2,
                Decimal("10"),
                Decimal("2"),
                Decimal("5"),
                Decimal("6"),
                Decimal("4"),
                currency="USD",
            )
            calc = store.put("calculation", roll.model_dump(mode="json"))
            return result_with_slots(
                ({"slot": 1, "kind": "candidate", "idea_ref": ref},) + unavailable()[1:],
                portfolio_section="Hold while checking marks; no action is an alternative.",
                warnings=("Exact source warning: do not assume margin offsets.",),
                supporting_refs=(calc,),
            )

    portfolio = snapshot(
        positions=[
            {
                "position_id": "short",
                "contract": option(multiplier="10"),
                "quantity": "-3",
                "currency": "USD",
            },
            {
                "position_id": "partial-hedge",
                "contract": option(instrument_id="LONG", multiplier="10", strike="45"),
                "quantity": "1",
                "currency": "USD",
            },
            {
                "position_id": "adjusted",
                "contract": option(instrument_id="ADJ", adjustment="split"),
                "quantity": "1",
                "currency": "USD",
            },
            {
                "position_id": "bond",
                "contract": {"instrument_id": "BOND", "instrument_type": "bond", "currency": "USD"},
                "quantity": "4",
                "currency": "USD",
            },
        ]
    )
    result = await run_team(
        TeamRequest(question="Combined", mode="combined", portfolio=portfolio),
        config=TeamConfig(
            model=ModelConfig(model_id="offline"),
            prompts=PromptSet.development(),
            output_dir=tmp_path,
        ),
        deps=TeamDependencies(role_factory=lambda role, context: Lead(context), clock=fixed_clock),
    )
    report = Path(result.artifacts["report"]).read_text()
    saved = json.loads(Path(result.artifacts["result"]).read_text())
    assert "Hold while checking marks" in report
    assert "Exact source warning: do not assume margin offsets." in report
    assert saved["warnings"] == ["Exact source warning: do not assume margin offsets."]
    for text in (
        "2026-10-29T20:00:00",
        "end_of_day",
        "fetched",
        "2026-10-31",
        "gross_assignment_funding",
        "5000",
        "old_leg_realized_total",
        "-60",
        "net_roll_cash",
        "16",
        "combined_history_cash",
        "56",
        "broker_margin: unknown",
        "short",
        "partial-hedge",
        "adjusted",
        "bond",
        "multiplier=10",
        "unsupported",
    ):
        assert text in report
    assert (
        datetime.fromisoformat(saved["started_at"])
        == datetime.fromisoformat(saved["completed_at"])
        == fixed_clock()
    )
    assert saved["model_identity"]["model_id"] == "offline"
    assert set(saved["prompt_identities"]) == {"team_lead", "analyst", "wheel_expert"}
    assert saved["findings"]


async def test_malformed_lead_output_preserves_truthful_failure_artifacts(tmp_path):
    class Broken:
        async def invoke_async(self, question):
            return {"summary": "ready to trade", "candidate_slots": "invented"}

    result = await run_team(
        TeamRequest(question="Discover", mode="discovery"),
        config=TeamConfig(
            model=ModelConfig(model_id="offline"),
            prompts=PromptSet.development(),
            output_dir=tmp_path,
        ),
        deps=TeamDependencies(role_factory=lambda role, context: Broken(), clock=fixed_clock),
    )
    assert result.completion_reason == "failed"
    report = Path(result.artifacts["report"]).read_text()
    assert "ready to trade" not in report
    assert "Run stopped before final synthesis" in report
    assert len(result.candidate_slots) == 5
    assert all(slot["kind"] == "unavailable" for slot in result.candidate_slots)


async def test_blocked_ready_label_is_local_and_portfolio_survives_discovery_failure(tmp_path):
    class Lead:
        def __init__(self, context):
            self.context = context

        async def invoke_async(self, question):
            ref = candidate(self.context.store, status=CandidateStatus.READY)
            return result_with_slots(
                ({"slot": 1, "kind": "candidate", "idea_ref": ref},) + unavailable()[1:],
                portfolio_section="Independent portfolio analysis survives.",
            )

    result = await run_team(
        TeamRequest(question="Combined", mode="combined", portfolio=snapshot()),
        config=TeamConfig(
            model=ModelConfig(model_id="offline"),
            prompts=PromptSet.development(),
            output_dir=tmp_path,
        ),
        deps=TeamDependencies(role_factory=lambda role, context: Lead(context), clock=fixed_clock),
    )
    assert result.completion_reason == "partial"
    report = result.report_markdown
    assert "### Slot 1: SYN — Do not execute now — unverified" in report
    assert "Lead-recorded status (unverified claim): Ready for user review" in report
    assert "Independent portfolio analysis survives." in report


def test_referenced_quality_missing_source_and_wrong_reference_kinds_are_findings(tmp_path):
    store = EvidenceStore(tmp_path)
    ref = candidate(store)
    idea = store.get(ref)
    idea["revision_id"] = "bad"
    idea["quality_ref"] = idea["evidence_refs"][0]
    bad = store.put("idea", idea)
    result = result_with_slots(
        ({"slot": 1, "kind": "candidate", "idea_ref": bad},) + unavailable()[1:]
    )
    findings = api().validate_result(
        result, store, TeamRequest(question="Discover", mode="discovery")
    )
    assert "invalid_support_reference" in {f.code for f in findings}


def test_source_projection_excludes_provider_documents_and_credentials(tmp_path):
    store = EvidenceStore(tmp_path)
    ref = store.put(
        "evidence",
        evidence(
            content={
                "premium": "2",
                "document": "private body",
                "api_key": "secret",
                "raw": {"nested": 42},
            }
        ),
    )
    result = TeamResult(summary="Explanation", supporting_refs=(ref,))
    projected = api().report_records(result, store)
    assert projected[ref]["quote"] == {"premium": "2"}
    assert "private body" not in json.dumps(projected)
    assert "secret" not in json.dumps(projected)


def test_unsupported_fluent_readiness_without_candidate_is_labeled(tmp_path):
    result = TeamResult(summary="Ready for user review", mode="focused")
    findings = api().validate_result(
        result, EvidenceStore(tmp_path), TeamRequest(question="Check current trade", mode="focused")
    )
    assert "unsupported_readiness_claim" in {f.code for f in findings}


def test_bad_calculation_cannot_hide_under_a_supported_candidate(tmp_path):
    store = EvidenceStore(tmp_path)
    ref = candidate(store)
    idea = store.get(ref)
    original = store.get(idea["calculation_refs"][0])
    original["calculation_id"] = "bad-calculation"
    original["outputs"]["gross_assignment_funding"] = "1"
    bad = store.put("calculation", original)
    idea["revision_id"] = "bad"
    idea["calculation_refs"] = [bad]
    ref = store.put("idea", idea)
    result = result_with_slots(
        ({"slot": 1, "kind": "candidate", "idea_ref": ref},) + unavailable()[1:]
    )
    result = result.model_copy(
        update={
            "findings": tuple(
                api().validate_result(
                    result, store, TeamRequest(question="Discover", mode="discovery")
                )
            ),
            "supporting_records": api().report_records(result, store),
        }
    )
    assert "SYN — Do not execute now — unverified (integrity blocked)" in api().render_result(
        result
    )
    assert "UNVERIFIED — mismatch" in api().render_result(result)


def test_validated_coverage_preserves_partial_hedge_and_unsupported_adjustment(tmp_path):
    from wheel_team.domain.coverage import match_coverage

    portfolio = snapshot(
        positions=[
            {
                "position_id": "short",
                "contract": option(multiplier="10"),
                "quantity": "-3",
                "currency": "USD",
            },
            {
                "position_id": "long",
                "contract": option(instrument_id="L", multiplier="10", strike="45"),
                "quantity": "1",
                "currency": "USD",
            },
            {
                "position_id": "adjusted",
                "contract": option(instrument_id="ADJ", adjustment="split"),
                "quantity": "1",
                "currency": "USD",
            },
        ]
    )
    store = EvidenceStore(tmp_path)
    record = match_coverage(portfolio)
    ref = store.put("calculation", record.model_dump(mode="json"))
    result = TeamResult(summary="Check coverage", mode="portfolio", supporting_refs=(ref,))
    findings = api().validate_result(
        result, store, TeamRequest(question="Check coverage", mode="portfolio", portfolio=portfolio)
    )
    assert "calculation_mismatch" not in {f.code for f in findings}
    assert "unsupported_coverage" in {f.code for f in findings}
    result = result.model_copy(
        update={
            "findings": tuple(findings),
            "portfolio_snapshot": portfolio,
            "supporting_records": api().report_records(result, store),
        }
    )
    rendered = api().render_result(result)
    assert "matched_option_contracts: 1" in rendered
    assert "unmatched_short_contracts: 2" in rendered
    assert "matched_strike_gap_exposure: 50" in rendered
    assert "broker_margin: unknown" in rendered


def test_missing_portfolio_is_reported_independently_of_discovery(tmp_path):
    result = result_with_slots(unavailable()).model_copy(update={"mode": "combined"})
    findings = api().validate_result(
        result, EvidenceStore(tmp_path), TeamRequest(question="Combined", mode="combined")
    )
    assert "portfolio_unavailable" in {f.code for f in findings}
    rendered = api().render_result(result.model_copy(update={"findings": tuple(findings)}))
    assert "## Discovery" in rendered
    assert "## Portfolio" in rendered
    assert "Portfolio snapshot unavailable" in rendered


def test_ready_requires_supported_host_snapshot_and_all_configured_checks(tmp_path):
    from wheel_team.config import PolicyConfig
    from wheel_team.domain.portfolio import analyze_portfolio

    store = EvidenceStore(tmp_path)
    ref = candidate(store, status=CandidateStatus.READY)
    idea = store.get(ref)
    live = store.put(
        "evidence",
        evidence(
            "live-quote",
            source_timestamp=fixed_clock(),
            fetched_at=fixed_clock(),
            delay_state="live",
            content={"instrument_id": "SYN-P50", "premium": "2"},
            units={"premium": "currency_per_share"},
        ),
    )
    quality = store.put(
        "quality",
        {
            "assessment_id": "live-quality",
            "company_id": "SYN",
            "assessed_at": fixed_clock(),
            "conclusion": "qualified",
            "rationale": "Synthetic quality only",
            "evidence_refs": [live],
        },
    )
    portfolio = snapshot(
        as_of=fixed_clock(),
        fetched_at=fixed_clock(),
        completeness="complete",
        positions=[],
        cash=[{"currency": "USD", "availability": "known", "amount": "100000"}],
        broker_capacity={
            "availability": "known",
            "source": "synthetic-broker",
            "as_of": fixed_clock(),
            "buying_power": {
                "availability": "known",
                "value": "100000",
                "unit": "currency",
                "currency": "USD",
            },
        },
    )
    snap_ref = store.put("snapshot", portfolio.model_dump(mode="json"))
    policy = PolicyConfig(
        max_issuer_weight="1",
        max_gross_assignment_to_nav="1",
        max_snapshot_age_seconds=60,
        max_quote_age_seconds=60,
        max_capacity_age_seconds=60,
        required_capacity_fields=("buying_power",),
    )
    calc = analyze_portfolio(portfolio, policy, now=fixed_clock())
    calc_ref = store.put("calculation", calc.model_dump(mode="json"))
    idea.update(
        revision_id="live",
        evidence_refs=[live],
        quality_ref=quality,
        snapshot_ref=snap_ref,
        calculation_refs=idea["calculation_refs"] + [calc_ref],
    )
    ref = store.put("idea", idea)
    result = result_with_slots(
        ({"slot": 1, "kind": "candidate", "idea_ref": ref, "quote_refs": [live]},)
        + unavailable()[1:],
        owner_policy=policy,
        completed_at=fixed_clock(),
    )
    findings = api().validate_result(
        result, store, TeamRequest(question="Discover", mode="discovery", portfolio=portfolio)
    )
    assert not [f for f in findings if f.severity == "error"]
    assert "readiness_unverified" not in {f.code for f in findings}
    result = result.model_copy(
        update={
            "findings": tuple(findings),
            "supporting_records": api().report_records(result, store),
        }
    )
    assert "### Slot 1: SYN — Ready for user review" in api().render_result(result)


def test_unavailable_slot_warning_is_preserved_in_report():
    slots = list(unavailable())
    slots[0]["warnings"] = ["Exact slot warning: coverage incomplete"]
    assert "Exact slot warning: coverage incomplete" in api().render_result(
        result_with_slots(tuple(slots))
    )


def test_alternative_support_is_checked_without_walking_provider_json(tmp_path):
    store = EvidenceStore(tmp_path)
    ref = candidate(store)
    idea = store.get(ref)
    idea["revision_id"] = "alternative"
    idea["alternatives"] = [
        {
            "description": "Synthetic roll",
            "rationale": "Compare old loss",
            "calculation_refs": ["calculation:missing"],
        }
    ]
    ref = store.put("idea", idea)
    result = result_with_slots(
        ({"slot": 1, "kind": "candidate", "idea_ref": ref},) + unavailable()[1:]
    )
    findings = api().validate_result(
        result, store, TeamRequest(question="Discover", mode="discovery")
    )
    assert "unresolved_reference" in {f.code for f in findings}
    result = result.model_copy(
        update={
            "findings": tuple(findings),
            "supporting_records": api().report_records(result, store),
        }
    )
    assert "SYN — Do not execute now — unverified (integrity blocked)" in api().render_result(
        result
    )


@pytest.mark.parametrize(
    "age,limit,expected",
    [
        (60, 60, None),
        (61, 60, "quote_stale"),
        (-1, 60, "quote_time_in_future"),
        (0, None, "quote_freshness_unconfigured"),
        (0, 0, None),
    ],
)
def test_quote_age_uses_host_cutoff_and_explicit_policy_not_fresh_fetch(
    tmp_path, age, limit, expected
):
    from datetime import timedelta

    from wheel_team.config import PolicyConfig

    store = EvidenceStore(tmp_path)
    ref = candidate(store)
    idea = store.get(ref)
    quote = store.put(
        "evidence",
        evidence(
            "age-quote",
            fetched_at=fixed_clock(),
            source_timestamp=fixed_clock() - timedelta(seconds=age),
            delay_state="live",
        ),
    )
    idea.update(revision_id="age", evidence_refs=[quote])
    ref = store.put("idea", idea)
    result = result_with_slots(
        ({"slot": 1, "kind": "candidate", "idea_ref": ref, "quote_refs": [quote]},)
        + unavailable()[1:],
        owner_policy=PolicyConfig(max_quote_age_seconds=limit),
        completed_at=fixed_clock(),
    )
    findings = api().validate_result(
        result, store, TeamRequest(question="Discover", mode="discovery")
    )
    codes = {f.code for f in findings}
    if expected:
        assert expected in codes
    else:
        assert not {"quote_stale", "quote_time_in_future", "quote_freshness_unconfigured"} & codes


def test_ancient_live_quote_does_not_become_current_on_fresh_fetch(tmp_path):
    from wheel_team.config import PolicyConfig

    store = EvidenceStore(tmp_path)
    ref = candidate(store, status=CandidateStatus.READY)
    idea = store.get(ref)
    quote = store.put(
        "evidence", evidence("ancient-live", fetched_at=fixed_clock(), delay_state="live")
    )
    idea.update(revision_id="ancient", evidence_refs=[quote])
    ref = store.put("idea", idea)
    result = result_with_slots(
        ({"slot": 1, "kind": "candidate", "idea_ref": ref, "quote_refs": [quote]},)
        + unavailable()[1:],
        owner_policy=PolicyConfig(max_quote_age_seconds=60),
        completed_at=fixed_clock(),
    )
    codes = {
        f.code
        for f in api().validate_result(
            result, store, TeamRequest(question="Discover", mode="discovery")
        )
    }
    assert {"quote_stale", "readiness_unverified"} <= codes


async def test_host_overwrites_model_claimed_owner_policy(tmp_path):
    from wheel_team.config import PolicyConfig

    class Lead:
        async def invoke_async(self, question):
            return TeamResult(
                summary="Explanation", owner_policy=PolicyConfig(max_quote_age_seconds=999)
            )

    result = await run_team(
        TeamRequest(question="Explain", mode="education"),
        config=TeamConfig(
            model=ModelConfig(model_id="offline"),
            prompts=PromptSet.development(),
            output_dir=tmp_path,
            policy=PolicyConfig(max_quote_age_seconds=5),
        ),
        deps=TeamDependencies(role_factory=lambda role, context: Lead(), clock=fixed_clock),
    )
    assert result.owner_policy.max_quote_age_seconds == 5


def test_focused_idea_support_resolves_direct_refs_and_renders_exact_terms(tmp_path):
    store = EvidenceStore(tmp_path)
    ref = candidate(store)
    idea = store.get(ref)
    idea.update(revision_id="focused", evidence_refs=["evidence:missing"])
    ref = store.put("idea", idea)
    result = TeamResult(summary="Focused research", mode="focused", supporting_refs=(ref,))
    findings = api().validate_result(
        result, store, TeamRequest(question="Research", mode="focused")
    )
    assert "unresolved_reference" in {f.code for f in findings}
    result = result.model_copy(
        update={
            "findings": tuple(findings),
            "supporting_records": api().report_records(result, store),
        }
    )
    assert "Terms: instrument_id=SYN-P50" in api().render_result(result)


def test_old_filing_does_not_make_selected_fresh_quote_stale(tmp_path):
    from wheel_team.config import PolicyConfig

    store = EvidenceStore(tmp_path)
    ref = candidate(store)
    idea = store.get(ref)
    fresh = store.put(
        "evidence",
        evidence(
            "fresh",
            source_timestamp=fixed_clock(),
            fetched_at=fixed_clock(),
            delay_state="live",
            content={"instrument_id": "SYN-P50", "bid": "2"},
            units={"bid": "currency_per_share"},
        ),
    )
    idea.update(revision_id="fresh-and-filing", evidence_refs=idea["evidence_refs"] + [fresh])
    ref = store.put("idea", idea)
    result = result_with_slots(
        ({"slot": 1, "kind": "candidate", "idea_ref": ref, "quote_refs": [fresh]},)
        + unavailable()[1:],
        owner_policy=PolicyConfig(max_quote_age_seconds=60),
        completed_at=fixed_clock(),
    )
    codes = {
        f.code
        for f in api().validate_result(
            result, store, TeamRequest(question="Discover", mode="discovery")
        )
    }
    assert (
        not {
            "quote_stale",
            "quote_delay_unverified",
            "quote_selection_unverified",
            "evidence_not_current",
            "invalid_candidate_slot",
        }
        & codes
    )


def test_quote_selection_must_belong_to_idea_and_identify_its_exact_contract(tmp_path):
    from wheel_team.config import PolicyConfig

    store = EvidenceStore(tmp_path)
    ref = candidate(store)
    foreign = store.put("evidence", evidence("foreign"))
    result = result_with_slots(
        ({"slot": 1, "kind": "candidate", "idea_ref": ref, "quote_refs": [foreign]},)
        + unavailable()[1:],
        owner_policy=PolicyConfig(max_quote_age_seconds=60),
        completed_at=fixed_clock(),
    )
    codes = {
        f.code
        for f in api().validate_result(
            result, store, TeamRequest(question="Discover", mode="discovery")
        )
    }
    assert "invalid_quote_reference" in codes


async def test_malformed_null_slot_warnings_still_save_truthful_partial_artifacts(tmp_path):
    slots = tuple(slot | {"warnings": None} for slot in unavailable())

    class Lead:
        async def invoke_async(self, question):
            return result_with_slots(slots, portfolio_section="Independent useful as-of analysis.")

    result = await run_team(
        TeamRequest(question="Combined", mode="combined", portfolio=snapshot()),
        config=TeamConfig(
            model=ModelConfig(model_id="offline"),
            prompts=PromptSet.development(),
            output_dir=tmp_path,
        ),
        deps=TeamDependencies(role_factory=lambda role, context: Lead(), clock=fixed_clock),
    )
    assert result.completion_reason == "partial"
    assert "invalid_candidate_slot" in {f.code for f in result.findings}
    saved = json.loads(Path(result.artifacts["result"]).read_text())
    report = Path(result.artifacts["report"]).read_text()
    assert saved["completion_reason"] == "partial"
    assert report.count("— unavailable") == 5
    assert "Independent useful as-of analysis." in report


def ready_case(
    store,
    *,
    snapshot_age=0,
    capacity_age=0,
    quote_premium="2",
    price_basis="premium",
    quality_source=None,
):
    """An otherwise supported Ready case with immutable earlier calculation audit."""
    from datetime import timedelta

    from wheel_team.config import PolicyConfig
    from wheel_team.domain.portfolio import analyze_portfolio

    cutoff = fixed_clock()
    quote = store.put(
        "evidence",
        evidence(
            "ready-quote",
            source_timestamp=cutoff,
            fetched_at=cutoff,
            delay_state="live",
            content={"instrument_id": "SYN-P50", "premium": quote_premium},
            units={"premium": "currency_per_share"},
        ),
    )
    source = (
        store.put("evidence", evidence("quality-source", **quality_source))
        if quality_source
        else quote
    )
    quality = store.put(
        "quality",
        {
            "assessment_id": "ready-quality",
            "company_id": "SYN",
            "assessed_at": cutoff,
            "conclusion": "qualified",
            "rationale": "Supported synthetic quality",
            "evidence_refs": [source],
        },
    )
    policy = PolicyConfig(
        max_issuer_weight="1",
        max_gross_assignment_to_nav="1",
        max_snapshot_age_seconds=60,
        max_capacity_age_seconds=60,
        max_quote_age_seconds=60,
        required_capacity_fields=("buying_power",),
    )
    portfolio = snapshot(
        as_of=cutoff - timedelta(seconds=snapshot_age),
        fetched_at=cutoff,
        completeness="complete",
        positions=[],
        cash=[{"currency": "USD", "availability": "known", "amount": "100000"}],
        broker_capacity={
            "availability": "known",
            "source": "synthetic-broker",
            "as_of": cutoff - timedelta(seconds=capacity_age),
            "buying_power": {
                "availability": "known",
                "value": "100000",
                "unit": "currency",
                "currency": "USD",
            },
        },
    )
    snapshot_ref = store.put("snapshot", portfolio.model_dump(mode="json"))
    old = analyze_portfolio(
        portfolio, policy, now=cutoff - timedelta(seconds=min(snapshot_age, capacity_age))
    )
    portfolio_ref = store.put("calculation", old.model_dump(mode="json"))
    put = put_economics(
        ContractIdentity.model_validate(option()),
        1,
        Decimal("2"),
        Decimal("3"),
        price_basis=price_basis,
    )
    put_ref = store.put("calculation", put.model_dump(mode="json"))
    idea = IdeaRevision(
        idea_id="ready",
        revision_id="r1",
        company_id="SYN",
        contract=option(),
        evidence_refs=(quote,),
        calculation_refs=(put_ref, portfolio_ref),
        quality_ref=quality,
        snapshot_ref=snapshot_ref,
        thesis="Useful supported research",
        countercase="Contrary evidence",
        status=CandidateStatus.READY,
        author="analyst",
        task_id="ready",
        created_at=cutoff,
    )
    idea_ref = store.put("idea", idea.model_dump(mode="json"))
    result = result_with_slots(
        ({"slot": 1, "kind": "candidate", "idea_ref": idea_ref, "quote_refs": [quote]},)
        + unavailable()[1:],
        owner_policy=policy,
        completed_at=cutoff,
    )
    return (
        result,
        TeamRequest(question="Discover", mode="discovery", portfolio=portfolio),
        portfolio_ref,
    )


@pytest.mark.parametrize("snapshot_age,capacity_age", [(61, 60), (60, 61), (61, 61)])
def test_ready_rechecks_snapshot_and_capacity_ages_at_host_cutoff_preserving_audit(
    tmp_path, snapshot_age, capacity_age
):
    store = EvidenceStore(tmp_path)
    result, request, ref = ready_case(store, snapshot_age=snapshot_age, capacity_age=capacity_age)
    original = store.get(ref)
    assert original["parameters"]["policy_checks"]["max_snapshot_age_seconds"] == "pass"
    assert original["parameters"]["policy_checks"]["max_capacity_age_seconds"] == "pass"
    findings = api().validate_result(result, store, request)
    assert "portfolio_freshness_unverified" in {f.code for f in findings}
    assert "calculation_mismatch" not in {f.code for f in findings}
    assert store.get(ref) == original
    rendered = api().render_result(
        result.model_copy(
            update={
                "findings": tuple(findings),
                "supporting_records": api().report_records(result, store),
            }
        )
    )
    assert "SYN — Do not execute now — unverified" in rendered
    assert "Useful supported research" in rendered


@pytest.mark.parametrize(
    "quote_premium,price_basis",
    [("0.01", "premium"), ("2", "bid"), ("2", "unspecified_input_not_executable")],
)
def test_ready_requires_recorded_put_premium_to_match_selected_quote_basis(
    tmp_path, quote_premium, price_basis
):
    store = EvidenceStore(tmp_path)
    result, request, _ = ready_case(store, quote_premium=quote_premium, price_basis=price_basis)
    findings = api().validate_result(result, store, request)
    assert "quote_economics_unverified" in {f.code for f in findings}
    rendered = api().render_result(
        result.model_copy(
            update={
                "findings": tuple(findings),
                "supporting_records": api().report_records(result, store),
            }
        )
    )
    assert "SYN — Do not execute now — unverified" in rendered
    assert "net_premium: 197" in rendered


def test_ready_boundary_and_selected_quote_economics_remain_supported(tmp_path):
    store = EvidenceStore(tmp_path)
    result, request, _ = ready_case(store, snapshot_age=60, capacity_age=60)
    findings = api().validate_result(result, store, request)
    assert not [finding for finding in findings if finding.severity == "error"]


def test_quality_source_failure_blocks_local_ready_heading(tmp_path):
    from datetime import timedelta

    store = EvidenceStore(tmp_path)
    result, request, _ = ready_case(
        store,
        quality_source={
            "source_timestamp": fixed_clock() + timedelta(seconds=1),
            "fetched_at": fixed_clock(),
        },
    )
    findings = api().validate_result(result, store, request)
    assert {"evidence_time_inconsistent", "evidence_time_in_future"} <= {f.code for f in findings}
    rendered = api().render_result(
        result.model_copy(
            update={
                "findings": tuple(findings),
                "supporting_records": api().report_records(result, store),
            }
        )
    )
    assert "SYN — Do not execute now — unverified" in rendered
    assert "### Slot 1: SYN — Ready for user review" not in rendered
