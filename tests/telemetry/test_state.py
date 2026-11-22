import json

from portfolio_management.telemetry.state import RunState


def event(kind, inv="lead", role="team_lead", second=0, **kw):
    return {
        "event": kind,
        "invocation_id": inv,
        "role": role,
        "at": f"2026-10-31T14:00:{second:02d}+00:00",
        **kw,
    }


def test_concurrent_same_role_generations_and_honest_usage():
    s = RunState()
    s.apply(
        [
            event("model_generation_started", "analyst:a:x", "analyst", generation=2),
            event("model_generation_started", "analyst:b:y", "analyst", generation=3),
            event("public_text", "analyst:a:x", "analyst", generation=2, text="AAPL interim"),
            event("public_text", "analyst:b:y", "analyst", generation=3, text="AMD interim"),
            event(
                "model_generation_finished",
                "analyst:a:x",
                "analyst",
                second=5,
                generation=2,
                elapsed_seconds=5,
                status="success",
                usage={"inputTokens": 100, "outputTokens": 10},
            ),
        ]
    )
    snap = s.snapshot()
    lanes = {i["id"]: i for i in snap["invocations"]}
    assert lanes["analyst:a:x"]["generations"][0]["text"] == "AAPL interim"
    assert lanes["analyst:b:y"]["generations"][0]["text"] == "AMD interim"
    assert snap["totals"]["unclosed_model_starts"] == 1
    assert snap["totals"]["known_output_tokens"] == 10
    assert snap["totals"]["peak_concurrency"] is None
    assert snap["totals"]["unknown_usage_calls"] == 1


def test_nested_tool_failures_separate_from_transport_and_handoff():
    s = RunState()
    s.apply(
        [
            event(
                "tool_call_started",
                name="consult_expert",
                call_id="c1",
                inputs={"question": "Review AAPL"},
            ),
            event("model_generation_started", "expert:1", "wheel_expert", second=1, generation=4),
            event(
                "model_generation_finished",
                "expert:1",
                "wheel_expert",
                second=2,
                generation=4,
                status="success",
                elapsed_seconds=1,
                stop_reason="end_turn",
            ),
            event(
                "tool_call_finished",
                second=3,
                name="consult_expert",
                call_id="c1",
                status="success",
                elapsed_seconds=3,
                result=[{"text": json.dumps({"assessment": "Exact expert narrative"})}],
            ),
            event(
                "tool_call_finished",
                name="fetch_sources",
                call_id="f1",
                status="success",
                result=[
                    {
                        "text": json.dumps(
                            {
                                "results": [
                                    {"status": "blocked"},
                                    {"status": "extraction_failed"},
                                    {"truncated": True},
                                ]
                            }
                        )
                    }
                ],
            ),
        ]
    )
    snap = s.snapshot()
    lead = next(i for i in snap["invocations"] if i["id"] == "lead")
    expert = next(i for i in snap["invocations"] if i["id"] == "expert:1")
    tool = next(t for t in lead["tools"] if t["call_id"] == "f1")
    assert tool["status"] == "success"
    assert set(tool["nested_statuses"]) >= {"blocked", "extraction_failed", "truncated"}
    assert expert["output"] == "Exact expert narrative"
    assert expert["parent_call_id"] == "c1"
    assert expert["handoff_question_chars"] == 11
    assert expert["handoff_chars"] is None


def test_exact_reports_idempotent_artifacts_and_partial_finalization():
    s = RunState()
    events = [
        event("model_generation_started", "analyst:a:x", "analyst", generation=1),
        event(
            "artifact_research",
            results=[{"job_id": "a", "outcome": "partial", "summary": "Exact\n AAPL  "}],
        ),
        event("artifact_report", text="Exact lead\n", sha256="proof"),
        event("run_completed", completion_reason="partial"),
    ]
    s.apply(events)
    s.apply([events[1]])
    snap = s.snapshot()
    a = next(i for i in snap["invocations"] if i["id"] == "analyst:a:x")
    assert a["output"] == "Exact\n AAPL  "
    assert a["status"] == "partial"
    assert snap["report"]["sha256"] == "proof"
    assert snap["totals"]["unclosed_model_starts"] == 1
    assert a["generations"][0]["status"] == "incomplete"


def test_unknown_token_measurements_are_not_zero_filled():
    s = RunState()
    s.apply([event("model_generation_started", generation=1)])
    assert s.snapshot()["totals"]["known_output_tokens"] is None
    assert s.snapshot()["totals"]["known_input_tokens"] is None
    s.apply(
        [
            event(
                "model_generation_finished",
                generation=1,
                usage={"inputTokens": 100},
                status="success",
            )
        ]
    )
    assert s.snapshot()["totals"]["known_input_tokens"] == 100
    assert s.snapshot()["totals"]["known_output_tokens"] is None


def test_runtime_failure_report_is_not_ordinary_lead_output():
    fallback = (
        "# Team response\n\nMode: combined; completion: failed\n"
        "Started: 2026-10-31T14:00:00+00:00; completed: 2026-10-31T14:00:05+00:00\n\n"
        "Run stopped before final synthesis.\n\n## Portfolio\n\nPortfolio judgment unavailable.\n"
    )
    s = RunState()
    s.apply(
        [
            event("model_generation_started", generation=45),
            event(
                "model_generation_finished",
                generation=45,
                second=5,
                status="failed",
                error_category="BadRequestError",
            ),
            event("artifact_report", text=fallback, sha256="source-proof"),
            event("run_completed", completion_reason="failed"),
        ]
    )
    lead = s.snapshot()["invocations"][0]
    assert lead["status"] == "failed"
    assert lead["output"] == fallback
    assert lead["output_kind"] == "runtime_fallback"
    assert lead["output_label"] == "Runtime fallback report"
    assert s.snapshot()["report"]["sha256"] == "source-proof"


def test_genuine_completed_public_report_keeps_ordinary_origin_despite_run_failure():
    text = "Exact completed AAPL public text\n  "
    s = RunState()
    s.apply(
        [
            event("model_generation_started", generation=1),
            event("public_text", generation=1, text=text, final=True),
            event(
                "model_generation_finished", generation=1, status="success", stop_reason="end_turn"
            ),
            event("artifact_report", text=text, sha256="exact"),
            event("run_completed", completion_reason="failed"),
        ]
    )
    lead = s.snapshot()["invocations"][0]
    assert lead["status"] == "failed"
    assert lead["output"] == text
    assert lead["output_kind"] == "ordinary"
    assert lead["output_label"] == "Returned ordinary output"


def test_runtime_fallback_without_known_wording_is_not_attributed_to_lead():
    # Do not use an English phrase match as proof of authorship.
    s = RunState()
    s.apply(
        [
            event("model_generation_started", generation=45),
            event("model_generation_finished", generation=45, status="failed"),
            event(
                "artifact_report",
                text="Runtime stopped; portfolio fallback preserved.",
                sha256="proof",
            ),
            event("run_completed", completion_reason="failed"),
        ]
    )
    lead = s.snapshot()["invocations"][0]
    assert lead["output_kind"] == "runtime_fallback"


def test_missing_model_finishes_make_overlap_unknown_across_later_phases():
    s = RunState()
    s.apply(
        [
            event("model_generation_started", "analyst:a:x", "analyst", generation=1),
            event("analyst_completed", job_id="a", outcome="budget_exhausted"),
            event("model_generation_started", "expert:1", "wheel_expert", second=2, generation=2),
            event(
                "tool_call_started",
                second=1,
                call_id="expert-call",
                name="consult_expert",
                inputs={},
            ),
            event(
                "tool_call_finished",
                second=3,
                call_id="expert-call",
                name="consult_expert",
                status="success",
                result=[
                    {
                        "text": json.dumps(
                            {
                                "assessment": "Expert did not complete",
                                "error": "time reserve",
                                "outcome": "failed",
                            }
                        )
                    }
                ],
            ),
            event("model_generation_started", second=4, generation=3),
            event("model_generation_finished", second=5, generation=3, status="failed"),
            event("run_completed", second=5, completion_reason="failed"),
        ]
    )
    snap = s.snapshot()
    assert snap["totals"]["peak_concurrency"] is None
    assert snap["totals"]["unclosed_model_starts"] == 2
    assert snap["totals"]["model_lifecycle_complete"] is False
    assert "active_models" not in snap["totals"]
    expert = next(i for i in snap["invocations"] if i["id"] == "expert:1")
    assert expert["status"] == "failed"
    assert expert["output_kind"] == "runtime_fallback"
    assert expert["output_label"] == "Runtime failure notice"


def test_balanced_trace_overlap_has_explicit_trace_scope():
    s = RunState()
    s.apply(
        [
            event("model_generation_started", "analyst:a:x", "analyst", generation=1),
            event("model_generation_started", "analyst:b:y", "analyst", second=1, generation=2),
            event(
                "model_generation_finished",
                "analyst:a:x",
                "analyst",
                second=2,
                generation=1,
                status="success",
            ),
            event(
                "model_generation_finished",
                "analyst:b:y",
                "analyst",
                second=3,
                generation=2,
                status="success",
            ),
            event("run_completed", second=4, completion_reason="complete"),
        ]
    )
    totals = s.snapshot()["totals"]
    assert totals["peak_concurrency"] == 2
    assert totals["model_lifecycle_complete"]
    assert totals["unclosed_model_starts"] == 0
    assert (
        totals["concurrency_scope"]
        == "SDK hook intervals; includes waiting; not server request concurrency"
    )


def test_structured_completed_lead_report_keeps_ordinary_origin_and_failed_status():
    s = RunState()
    text = "Exact returned lead narrative\n  "
    s.apply(
        [
            event("model_generation_started", generation=1),
            event(
                "model_generation_finished", generation=1, status="success", stop_reason="tool_use"
            ),
            event(
                "tool_call_started",
                call_id="output",
                name="TeamResult",
                inputs={"summary": text, "completion_reason": "failed"},
            ),
            event("tool_call_finished", call_id="output", name="TeamResult", status="success"),
            event("artifact_report", text=text, sha256="proof"),
            event("run_completed", completion_reason="failed"),
        ]
    )
    lead = s.snapshot()["invocations"][0]
    assert lead["status"] == "failed"
    assert lead["output"] == text
    assert lead["output_kind"] == "ordinary"


def test_structured_expert_failure_assessment_is_genuine_model_output():
    s = RunState()
    assessment = "Genuine structured expert assessment"
    result = {"assessment": assessment, "error": "Insufficient evidence"}
    s.apply(
        [
            event(
                "tool_call_started", second=0, call_id="consult", name="consult_expert", inputs={}
            ),
            event("model_generation_started", "expert:1", "wheel_expert", second=1, generation=2),
            event(
                "model_generation_finished",
                "expert:1",
                "wheel_expert",
                second=2,
                generation=2,
                status="success",
                stop_reason="tool_use",
            ),
            event(
                "tool_call_started",
                "expert:1",
                "wheel_expert",
                second=2,
                call_id="expert-output",
                name="ExpertResult",
                inputs=result,
            ),
            event(
                "tool_call_finished",
                "expert:1",
                "wheel_expert",
                second=2,
                call_id="expert-output",
                name="ExpertResult",
                status="success",
            ),
            event(
                "tool_call_finished",
                second=3,
                call_id="consult",
                name="consult_expert",
                status="success",
                result=[{"text": json.dumps(result)}],
            ),
            event("run_completed", second=4, completion_reason="failed"),
        ]
    )
    expert = next(i for i in s.snapshot()["invocations"] if i["id"] == "expert:1")
    assert expert["status"] == "failed"
    assert expert["output"] == assessment
    assert expert["output_kind"] == "ordinary"
    assert expert["output_provenance"] == "exact consult_expert returned assessment"
