"""One offline SDK invocation exercises probe finalization, never live acceptance."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.support.thin_sdk import ScriptModel, output
from wheel_team.config import ModelConfig, PromptSet, TeamConfig
from wheel_team.contracts import EvidenceRecord


def inputs(tmp_path):
    bundle = tmp_path / "source-bundle"
    source = bundle / "runs" / "unit-fixture"
    records = source / "records"
    records.mkdir(parents=True)
    (source / "manifest.json").write_text(
        '{"run_id":"unit-fixture","started_at":"2026-10-31T00:00:00Z"}'
    )
    refs = []
    for identity, content in [
        ("unit-quote", {"capability": "underlying_quotes", "data": {}}),
        ("unit-fundamentals", {"capability": "fundamentals", "data": {}}),
        ("unit-fetch", {"operation": "fetch", "text": "Unit fixture; no issuer facts. " * 200}),
    ]:
        # These labelled fixtures make no financial or issuer factual claims.
        record = EvidenceRecord(
            evidence_id=identity,
            source="labelled-unit-fixture",
            fetched_at="2026-10-31T00:00:00Z",
            delay_state="unknown",
            source_url="https://example.invalid/labelled-unit-fixture",
            content=content,
        )
        ref = "evidence:" + identity
        refs.append(ref)
        path = records / (hashlib.sha256(ref.encode()).hexdigest() + ".json")
        path.write_text(
            json.dumps({"kind": "evidence", "ref": ref, "record": record.model_dump(mode="json")})
        )
    index = {
        str(path.relative_to(bundle)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [source / "manifest.json", *records.glob("*.json")]
    }
    (bundle / "finalization-bundle-index.json").write_text(json.dumps(index))
    prompt = tmp_path / "approved-unit-prompt.md"
    prompt.write_text("Explicit unit-test prompt; not an owner production asset.")
    manifest = tmp_path / "prompts.json"
    manifest.write_text(
        json.dumps(
            {
                "prompt_set_id": "unit-only",
                "version": "1",
                "status": "approved",
                "owner_approval_ref": "unit-test-fixture-only",
                "roles": {
                    "analyst": {
                        "path": prompt.name,
                        "content_hash": hashlib.sha256(prompt.read_bytes()).hexdigest(),
                    }
                },
            }
        )
    )
    config = TeamConfig(
        model=ModelConfig(model_id="unit-only", max_output_tokens=8192),
        prompts=PromptSet(manifest_path=manifest),
        max_generations=1,
        deadline_seconds=1,
    )
    return config, source, refs


def test_prepare_copies_hash_bound_inputs_without_constructing_a_model(tmp_path, monkeypatch):
    from wheel_team.evaluation import final_output_probe as probe

    config, source, refs = inputs(tmp_path)
    before = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    monkeypatch.setattr(probe, "build_model", lambda *_: pytest.fail("prepare constructed a model"))
    prepared = probe.prepare_probe(config, source, tmp_path / "prepared", refs)
    assert prepared["diagnostic_only"] is True
    assert prepared["live_acceptance"] is False
    assert prepared["selected_refs"] == refs
    assert prepared["question_chars"] < 7000
    assert prepared["prompt_identity"]["status"] == "approved"
    assert prepared["recorded_analysis_time"] == "2026-10-31T00:00:00Z"
    assert prepared["limits"] == {
        "max_generations": 1,
        "deadline_seconds": 1.0,
        "max_output_tokens": 8192,
    }
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert len(list((tmp_path / "prepared" / "records").glob("*.json"))) == 3
    assert "untrusted" in (tmp_path / "prepared" / "question.txt").read_text().lower()
    assert prepared["selected_inputs"][2]["projection"]["content"]["omitted_text_chars"] > 0


def test_changed_source_record_is_rejected_before_probe_artifacts(tmp_path):
    from wheel_team.evaluation import final_output_probe as probe

    config, source, refs = inputs(tmp_path)
    path = next((source / "records").glob("*.json"))
    path.write_text(path.read_text() + " ")
    destination = tmp_path / "rejected"
    with pytest.raises(ValueError, match="hash"):
        probe.prepare_probe(config, source, destination, refs)
    assert not destination.exists()


async def test_real_sdk_probe_has_only_output_tool_and_preserves_approved_prefix(tmp_path):
    from wheel_team.evaluation import final_output_probe as probe

    config, source, refs = inputs(tmp_path)

    class InspectModel(ScriptModel):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            self.prefix = system_prompt
            async for chunk in super().stream(messages, tool_specs, system_prompt, **kwargs):
                yield chunk

    model = InspectModel(
        output(
            "ResearchResult",
            job_id="pep-final-output-probe",
            outcome="partial",
            summary="No financial claims from unit fixtures.",
            evidence_refs=refs,
        )
    )
    summary = await probe.run_probe(config, source, tmp_path / "run", refs, model=model)
    assert summary["status"] == "validated"
    assert summary["diagnostic_pass"] is True
    assert summary["accepted_generations"] == 1
    assert summary["final_tool_validation"] == {"success": 1, "error": 0}
    assert summary["model_events"][0]["stop_reason"] == "tool_use"
    assert summary["model_events"][0]["usage"] == {
        "inputTokens": 2,
        "outputTokens": 3,
        "totalTokens": 5,
    }
    assert summary["diagnostic_only"] and not summary["live_acceptance"]
    assert summary["provider_telemetry"]["observed"] is False
    assert summary["provider_telemetry"]["usage"] is None
    assert summary["provider_telemetry"]["visible_text_chars"] is None
    assert {x["name"] for x in model.calls[0]["tools"]} == {"ResearchResult"}
    assert model.prefix.startswith("Explicit unit-test prompt; not an owner production asset.\n\n")
    assert "reasoning" not in json.dumps(summary)


async def test_three_probe_conditions_have_identical_formatted_input_despite_dirs_and_clocks(
    tmp_path, monkeypatch
):
    from datetime import UTC, datetime

    from wheel_team.evaluation import final_output_probe as probe
    from wheel_team.model_observer import CompletionTelemetry, ObservedOpenAIModel

    config, source, refs = inputs(tmp_path)
    clock = [datetime(2026, 11, 1, tzinfo=UTC)]

    class Clock:
        @staticmethod
        def now(tz):
            return clock[0]

    monkeypatch.setattr(probe, "datetime", Clock)
    snapshots, contexts = [], []

    class HashingModel(ScriptModel):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            contexts.append(json.loads(system_prompt.split("Run context (data):\n")[1]))
            self.formatter.format_request(
                messages, tool_specs, system_prompt, kwargs.get("tool_choice")
            )
            async for chunk in super().stream(messages, tool_specs, system_prompt, **kwargs):
                yield chunk

    for index, controls in enumerate(
        [
            {"max_tokens": 8192, "extra_body": {"include_reasoning": False}},
            {
                "max_tokens": 8192,
                "reasoning_effort": "low",
                "extra_body": {"include_reasoning": False},
            },
            {
                "max_tokens": 8192,
                "extra_body": {
                    "include_reasoning": False,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            },
        ]
    ):
        clock[0] = datetime(2026, 11, index + 1, tzinfo=UTC)
        telemetry = CompletionTelemetry()
        model = HashingModel(
            output(
                "ResearchResult",
                job_id="pep-final-output-probe",
                outcome="partial",
                summary="Unit fixture only.",
                evidence_refs=refs,
            )
        )
        model.formatter = ObservedOpenAIModel(
            telemetry=telemetry, model_id="unit-only", params=controls
        )
        await probe.run_probe(config, source, tmp_path / f"condition-{index}", refs, model=model)
        snapshots.append(telemetry.snapshot())
    assert len({row["formatted_input_sha256"] for row in snapshots}) == 1
    assert all(row["analysis_time"] == "2026-10-31T00:00:00Z" for row in contexts)
    assert {row["run_id"] for row in contexts} == {"recorded-final-output-probe"}
    assert all("runtime_budget" not in row for row in contexts)
    assert all(
        row["diagnostic_limits"]
        == {"max_generations": 1, "deadline_seconds": 1.0, "max_output_tokens": 8192}
        for row in contexts
    )


def test_schema_valid_but_long_wrongly_cited_result_fails_diagnostic_and_cli(tmp_path, monkeypatch):
    from wheel_team.evaluation import final_output_probe as probe

    config, source, refs = inputs(tmp_path)
    model = ScriptModel(
        output(
            "ResearchResult",
            job_id="wrong-job",
            outcome="partial",
            summary=" ".join(["word"] * 151),
            evidence_refs=["evidence:not-selected"],
        )
    )
    original = probe.run_probe

    async def offline(config, source_run, output_dir, refs):
        return await original(config, source_run, output_dir, refs, model=model)

    monkeypatch.setattr(probe, "run_probe", offline)
    path = tmp_path / "config.json"
    path.write_text(config.model_dump_json())
    monkeypatch.setattr(
        "sys.argv",
        [
            "probe",
            "--invoke",
            "--config",
            str(path),
            "--source-run",
            str(source),
            "--output",
            str(tmp_path / "run"),
            "--refs",
            *refs,
        ],
    )
    assert probe.main() == 1
    summary = json.loads((tmp_path / "run/probe-summary.json").read_text())
    assert summary["status"] == "validated"
    assert summary["diagnostic_pass"] is False
    checks = summary["format_checks"]
    assert checks["schema_valid"] is True
    assert checks["summary_word_count"] == 151
    assert checks["summary_within_150_words"] is False
    assert checks["expected_job_id"] is False
    assert checks["cited_refs_subset_of_selected"] is False
    assert len(model.calls) == 1


def test_projection_reports_field_omissions_and_nonoverlapping_text_excerpts():
    from wheel_team.evaluation import final_output_probe as probe

    quote = probe._projection(
        {
            "content": {
                "capability": "underlying_quotes",
                "data": {"currency": "USD", "unselected": "unit-only"},
            }
        }
    )["content"]
    assert quote["selected_data_fields"] == ["currency"]
    assert quote["original_field_count"] == 2
    assert quote["omitted_field_count"] == 1
    text = "x" * 1300
    excerpt = probe._projection({"content": {"operation": "fetch", "text": text}})["content"]
    assert len(excerpt["text_head"]) == 1000
    assert len(excerpt["text_tail"]) == 300
    assert excerpt["text_head"] + excerpt["text_tail"] == text


@pytest.mark.parametrize(
    "max_tokens,status,stop_reason",
    [(True, "max_tokens", "max_tokens"), (False, "generation", "end_turn")],
)
async def test_max_tokens_or_missing_output_never_silently_dispatches_a_second_request(
    tmp_path, max_tokens, status, stop_reason
):
    from wheel_team.evaluation import final_output_probe as probe

    config, source, refs = inputs(tmp_path)

    class StopModel(ScriptModel):
        async def stream(self, *args, **kwargs):
            async for chunk in super().stream(*args, **kwargs):
                if max_tokens and "messageStop" in chunk:
                    chunk = {"messageStop": {"stopReason": "max_tokens"}}
                yield chunk

    model = StopModel({"text": "No structured output."})
    summary = await probe.run_probe(config, source, tmp_path / "run", refs, model=model)
    assert summary["status"] == status
    assert summary["accepted_generations"] == len(model.calls) == 1
    assert summary["final_tool_validation"] == {"success": 0, "error": 0}
    assert summary["model_events"][0]["stop_reason"] == stop_reason
    assert summary["result"] is None


async def test_failed_final_output_tool_is_distinct_from_native_max_tokens(tmp_path):
    from wheel_team.evaluation import final_output_probe as probe

    config, source, refs = inputs(tmp_path)
    model = ScriptModel(output("ResearchResult"))
    summary = await probe.run_probe(config, source, tmp_path / "run", refs, model=model)
    assert summary["status"] == "generation"
    assert summary["accepted_generations"] == 1
    assert summary["final_tool_validation"] == {"success": 0, "error": 1}
    assert summary["model_events"][0]["stop_reason"] == "tool_use"


def test_real_private_attempt4_selection_hashes(tmp_path):
    from wheel_team.cli import load_team_config
    from wheel_team.evaluation import final_output_probe as probe

    fixture = os.environ.get("WHEEL_FINAL_OUTPUT_PROBE_FIXTURE")
    if not fixture:
        pytest.skip("Explicit private attempt-4 selection/config is required")
    selected = json.loads(Path(fixture).read_text())
    config = load_team_config(Path(selected["config"]))
    prepared = probe.prepare_probe(
        config, Path(selected["source_run"]), tmp_path / "real", selected["refs"]
    )
    assert prepared["source_run_id"] == "run-df6de88abc25418e81db8d5cfe35ed5d"
    assert len(prepared["selected_inputs"]) == 3
    assert all(row["file_sha256"] for row in prepared["selected_inputs"])
