"""Exact searchable originals and explicit maintenance, all in temporary private stores."""

import json
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

from tests.support.thin_sdk import output
from tests.thin.test_continuity_handoff import cfg
from tests.thin.test_fact_check_team import Capture
from portfolio_management.continuity import ContinuityFiles
from portfolio_management.owner_feedback import FeedbackSources, run_feedback
from portfolio_management.team import run_team
from portfolio_management.types import TeamDependencies, TeamRequest


def payload(text="  Owner α\r\nOriginal feedback.  "):
    return {
        "text": text,
        "platform": "telegram",
        "message_at": "2026-11-05T12:00:00Z",
        "transport_key": "private-chat/message-1",
    }


def test_timestamped_searchable_exact_source_and_duplicate(tmp_path):
    store = FeedbackSources(tmp_path, clock=lambda: datetime(2026, 11, 5, 12, 3, tzinfo=UTC))
    first = store.capture(payload())
    again = store.capture(payload())
    assert first["source_ref"] == again["source_ref"]
    assert store.original(first["source_ref"]) == payload()["text"]
    assert "2026-11-05" in first["source_ref"]
    assert first["recorded_at"] == "2026-11-05T12:03:00+00:00"
    assert first["message_at"] == "2026-11-05T12:00:00Z"
    match = ContinuityFiles(tmp_path).continuity_search(
        "Original feedback", path="sources/feedback/"
    )["matches"]
    assert len(match) == 1 and match[0]["path"] == first["source_ref"]


async def test_unknown_parent_preserves_before_clarification_and_no_model(tmp_path):
    def no_model(*_):
        raise AssertionError("No model until context is resolved")

    result = await run_feedback(
        payload() | {"parent_review": "/missing/old-report"},
        config=cfg(tmp_path),
        deps=TeamDependencies(model_factory=no_model),
    )
    assert result["source_preserved"] is True
    assert result["status"] == "needs_context"
    assert FeedbackSources(tmp_path / "durable").original(result["source_ref"]) == payload()["text"]


async def test_host_maintains_even_when_lead_omits_memory_and_preserves_attribution(tmp_path):
    config = cfg(tmp_path)
    parent = await run_team(
        TeamRequest(
            question="Original review", mode="portfolio", portfolio_text="Dated original book"
        ),
        config=config,
        deps=TeamDependencies(model_factory=lambda *_: Capture({"text": "Original report"})),
    )
    owner = "I would hold it for this reason. What do you think?"
    lead = Capture(output("consult_expert"), {"text": "Lead coordination summary"})
    expert = Capture({"text": "Exact Expert judgment α\r\nUncertainty retained."})
    memory = Capture(
        output(
            "continuity_write",
            path="vault/owner-notes.md",
            content="Owner reason and Expert judgment remain separate; execution unknown.",
            expected_revision=None,
        ),
        {"text": "Saved attributed discussion"},
    )
    models = deque([lead, expert, memory])
    result = await run_feedback(
        payload(owner) | {"parent_review": str(Path(parent.artifacts["result"]).parent)},
        config=config,
        deps=TeamDependencies(model_factory=lambda *_, models=models: models.popleft()),
    )
    assert not models and result["source_preserved"]
    assert result["maintenance"]["writes"][0]["path"] == "vault/owner-notes.md"
    packet = json.loads(memory.calls[0]["messages"][0]["content"][0]["text"])
    assert packet["originals"]["question"]["text"] == owner
    assert (
        packet["originals"]["expert_assessments"][0]["text"]
        == "Exact Expert judgment α\r\nUncertainty retained."
    )
    assert packet["originals"]["orchestrator_response"]["text"] == "Lead coordination summary"
    assert (
        packet["originals"]["orchestrator_response"]["attribution"]
        == "Lead coordination response; not an Expert assessment"
    )
    exchange = Path(config.continuity.root / result["discussion_ref"]).read_bytes().decode()
    assert owner in exchange and "Exact Expert judgment" in exchange
    duplicate = await run_feedback(
        payload(owner) | {"parent_review": str(Path(parent.artifacts["result"]).parent)},
        config=config,
        deps=TeamDependencies(
            model_factory=lambda *_: (_ for _ in ()).throw(AssertionError("duplicate"))
        ),
    )
    assert (
        duplicate["duplicate"] is True and duplicate["discussion_ref"] == result["discussion_ref"]
    )


async def test_record_only_uses_no_expert_and_failed_write_is_honest(tmp_path):
    memory = Capture({"text": "Unable to save notes; source remains preserved"})
    result = await run_feedback(
        payload() | {"action": "record"},
        config=cfg(tmp_path),
        deps=TeamDependencies(model_factory=lambda *_: memory),
    )
    assert len(memory.calls) == 1
    assert result["source_preserved"] and result["maintenance"]["writes"] == []
    assert result["notes_written"] is False


async def test_preserved_original_survives_invalid_prior_source(tmp_path):
    result = await run_feedback(
        payload() | {"prior_source": "sources/feedback/missing.md"}, config=cfg(tmp_path)
    )
    assert result["source_preserved"] and result["status"] == "needs_context"
    assert (
        FeedbackSources(tmp_path / "durable").original(result["current_source_ref"])
        == payload()["text"]
    )


async def test_budget_exhaustion_does_not_start_maintenance_model_or_lose_source(tmp_path):
    config = cfg(tmp_path)
    # One permitted generation is consumed by the lead; maintenance shares it.
    config = config.model_copy(update={"max_generations": 1, "max_expert_consultations": 0})
    (tmp_path / "parent").mkdir()
    parent_config = cfg(tmp_path / "parent")
    parent = await run_team(
        TeamRequest(question="Prior", mode="portfolio"),
        config=parent_config,
        deps=TeamDependencies(model_factory=lambda *_: Capture({"text": "Prior report"})),
    )
    constructed = []

    def factory(*_):
        constructed.append(1)
        return Capture({"text": "Discussion answer"})

    result = await run_feedback(
        payload() | {"parent_review": str(Path(parent.artifacts["result"]).parent)},
        config=config,
        deps=TeamDependencies(model_factory=factory),
    )
    assert result["source_preserved"]
    assert result["maintenance"]["completed"] is False
    assert result["maintenance"]["error"] and not result["notes_written"]
    assert len(constructed) == 1


async def test_clarification_and_next_discussion_keep_exact_originals(tmp_path):
    config = cfg(tmp_path)
    first = await run_feedback(payload("Original unresolved question"), config=config)
    parent = await run_team(
        TeamRequest(question="Prior", mode="portfolio"),
        config=config,
        deps=TeamDependencies(model_factory=lambda *_: Capture({"text": "Prior report"})),
    )
    lead = Capture({"text": "Discussed original question"})
    memory = Capture({"text": "No saved note claim"})
    models = deque([lead, memory])
    second = await run_feedback(
        payload("I mean the earlier dated report")
        | {
            "transport_key": "message-2",
            "prior_source": first["source_ref"],
            "parent_review": str(Path(parent.artifacts["result"]).parent),
        },
        config=config,
        deps=TeamDependencies(model_factory=lambda *_, models=models: models.popleft()),
    )
    assert lead.calls[0]["messages"][-1]["content"][0]["text"] == "Original unresolved question"
    assert "I mean the earlier dated report" not in lead.system
    assert "I mean the earlier dated report" in lead.calls[0]["messages"][0]["content"][0]["text"]
    next_lead = Capture({"text": "Next discussion"})
    next_memory = Capture({"text": "No saved note claim"})
    models = deque([next_lead, next_memory])
    await run_feedback(
        payload("Continuing this discussion")
        | {
            "transport_key": "message-3",
            "parent_review": str(Path(parent.artifacts["result"]).parent),
            "context_paths": [second["discussion_ref"]],
        },
        config=config,
        deps=TeamDependencies(model_factory=lambda *_, models=models: models.popleft()),
    )
    assert "Original unresolved question" not in next_lead.system
    assert "Original unresolved question" in next_lead.calls[0]["messages"][0]["content"][0]["text"]
    assert "Discussed original question" not in next_lead.system
    assert "Discussed original question" in next_lead.calls[0]["messages"][0]["content"][0]["text"]


async def test_expert_failure_still_preserves_owner_feedback_for_maintenance(tmp_path):
    config = cfg(tmp_path)
    parent = await run_team(
        TeamRequest(question="Prior", mode="portfolio"),
        config=config,
        deps=TeamDependencies(model_factory=lambda *_: Capture({"text": "Prior report"})),
    )
    lead = Capture(output("consult_expert"), {"text": "Expert failed; owner statement retained"})
    expert = Capture(RuntimeError("Expert unavailable"))
    memory = Capture({"text": "Owner statement remains distinct; no Expert judgment invented"})
    models = deque([lead, expert, memory])
    result = await run_feedback(
        payload() | {"parent_review": str(Path(parent.artifacts["result"]).parent)},
        config=config,
        deps=TeamDependencies(model_factory=lambda *_, models=models: models.popleft()),
    )
    packet = json.loads(memory.calls[0]["messages"][0]["content"][0]["text"])
    assert result["source_preserved"]
    assert packet["originals"]["question"]["text"] == payload()["text"]
    assert packet["originals"]["expert_assessments"] == []


def test_cli_record_roundtrip_is_exact_and_offline(tmp_path):
    import subprocess
    import sys

    config = cfg(tmp_path)
    config = config.model_copy(
        update={
            "model": config.model.model_copy(
                update={
                    "script": {
                        "role_scripts": {
                            "continuity": [[{"text": "Source preserved; no note write claimed"}]]
                        }
                    }
                }
            )
        }
    )
    path = tmp_path / "config.json"
    path.write_text(config.model_dump_json())
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "from portfolio_management.cli import main;raise SystemExit(main())",
            "feedback",
            "--config",
            str(path),
            "--output",
            str(tmp_path / "out"),
        ],
        input=json.dumps(payload() | {"action": "record"}, ensure_ascii=False),
        text=True,
        capture_output=True,
    )
    assert child.returncode == 0, child.stderr
    outcome = json.loads(child.stdout)
    assert outcome["source_preserved"] and outcome["maintenance"]["completed"]
    assert (
        FeedbackSources(tmp_path / "durable").original(outcome["source_ref"]) == payload()["text"]
    )


async def test_multiple_clarifications_have_distinct_discussions_and_honest_duplicate(tmp_path):
    config = cfg(tmp_path)
    original = await run_feedback(payload("Unresolved original"), config=config)
    parent = await run_team(
        TeamRequest(question="Prior", mode="portfolio"),
        config=config,
        deps=TeamDependencies(model_factory=lambda *_: Capture({"text": "Prior"})),
    )
    receipts = []
    for n in [2, 3]:
        memory = Capture(
            output(
                "continuity_write",
                path=f"vault/n{n}.md",
                content=f"Note {n}",
                expected_revision=None,
            ),
            {"text": "Saved"},
        )
        models = deque([Capture({"text": f"Discussion {n}"}), memory])
        current = payload(f"Clarification {n}") | {
            "transport_key": f"message-{n}",
            "prior_source": original["source_ref"],
            "parent_review": str(Path(parent.artifacts["result"]).parent),
        }
        receipt = await run_feedback(
            current,
            config=config,
            deps=TeamDependencies(model_factory=lambda *_, models=models: models.popleft()),
        )
        assert receipt["notes_written"] and receipt["maintenance"]["completed"]
        exchange = (config.continuity.root / receipt["discussion_ref"]).read_text()
        assert original["source_ref"] in exchange and receipt["current_source_ref"] in exchange
        assert f"Clarification {n}" in exchange
        receipts.append(receipt)
        replay = await run_feedback(
            current,
            config=config,
            deps=TeamDependencies(
                model_factory=lambda *_: (_ for _ in ()).throw(AssertionError("duplicate"))
            ),
        )
        assert replay["duplicate"] and replay["maintenance"] == receipt["maintenance"]
    assert receipts[0]["discussion_ref"] != receipts[1]["discussion_ref"]


async def test_archive_failure_preserves_confirmed_native_and_write_receipts(tmp_path, monkeypatch):
    config = cfg(tmp_path)

    def broken(*_):
        raise OSError("Archive unavailable")

    monkeypatch.setattr(FeedbackSources, "discussion", broken)
    memory = Capture(
        output(
            "continuity_write", path="vault/saved.md", content="Actual note", expected_revision=None
        ),
        {"text": "Saved"},
    )
    current = payload() | {"action": "record"}
    receipt = await run_feedback(
        current, config=config, deps=TeamDependencies(model_factory=lambda *_: memory)
    )
    assert receipt["notes_written"] and receipt["maintenance"]["completed"]
    assert receipt["run_id"] and receipt["result_path"]
    assert receipt["archive_error"] == "OSError" and receipt["status"] == "partial"
    assert (config.continuity.root / "vault/saved.md").exists()
    replay = await run_feedback(
        current,
        config=config,
        deps=TeamDependencies(
            model_factory=lambda *_: (_ for _ in ()).throw(AssertionError("duplicate"))
        ),
    )
    assert replay["duplicate"] and replay["maintenance"] == receipt["maintenance"]
    assert replay["notes_written"] and replay["archive_error"] == "OSError"


def test_selected_vault_context_is_versioned_derived_note_not_owner_original(tmp_path):
    store = FeedbackSources(tmp_path)
    store.files.writable = True
    store.files.continuity_write("vault/owner-note.md", "Dated agent note; not owner words", None)
    context = store.context(["vault/owner-note.md"])
    assert "Continuity-authored derived note" in context
    assert "Selected context path: vault/owner-note.md" in context
    assert "Revision: " in context and "Captured at: " in context
    snapshot = context.split("Snapshot: ", 1)[1].split("\n", 1)[0]
    store.files.continuity_write(
        "vault/owner-note.md",
        "Changed note",
        store.files.continuity_read("vault/owner-note.md")["revision"],
    )
    assert store.files._raw(snapshot).decode() == "Dated agent note; not owner words"


async def test_invalid_context_does_not_reserve_and_corrected_same_original_runs_once(tmp_path):
    config = cfg(tmp_path)
    current = payload() | {"action": "record", "context_paths": [".feedback/secret.json"]}
    bad = await run_feedback(
        current,
        config=config,
        deps=TeamDependencies(
            model_factory=lambda *_: (_ for _ in ()).throw(AssertionError("preflight"))
        ),
    )
    assert bad["source_preserved"] and bad["status"] == "needs_context"
    assert "context" in bad["error"].lower()
    assert not list((config.continuity.root / ".feedback-work").glob("*.json"))
    memory = Capture({"text": "Owner source preserved; no note write"})
    corrected = await run_feedback(
        {**current, "context_paths": []},
        config=config,
        deps=TeamDependencies(model_factory=lambda *_: memory),
    )
    assert corrected["maintenance"]["completed"] and len(memory.calls) == 1
    duplicate = await run_feedback(
        {**current, "context_paths": []},
        config=config,
        deps=TeamDependencies(
            model_factory=lambda *_: (_ for _ in ()).throw(AssertionError("duplicate"))
        ),
    )
    assert duplicate["duplicate"] and duplicate["run_id"] == corrected["run_id"]
    cached = await run_feedback(current, config=config)
    assert cached["duplicate"] and cached["run_id"] == corrected["run_id"]


def test_multimodal_caption_media_and_derived_transcription_remain_distinct(tmp_path):
    media = tmp_path / "cache"
    media.mkdir()
    image = media / "original.jpg"
    image.write_bytes(b"actual-image-bytes")
    store = FeedbackSources(tmp_path / "memory", media_roots=[media])
    original = payload("Exact owner caption") | {
        "input_attribution": "Original owner caption/text",
        "transformed_input": "Generated description misreads IVM",
        "transcription": "Visual reading: IWM Dec11 255 Put sold1 at3.49",
        "attachment_paths": [str(image)],
    }
    record = store.capture(original)
    assert store.original(record["source_ref"]) == "Exact owner caption"
    context = store.context([record["derived_source_ref"]])
    assert "Generated description misreads IVM" in context and "Visual reading: IWM" in context
    assert "derived" in context.lower() and "not verbatim owner" in context.lower()
    asset = tmp_path / "memory" / record["attachments"][0]["path"]
    assert asset.read_bytes() == image.read_bytes()
    assert store.capture(original)["source_ref"] == record["source_ref"]


def test_attachment_private_root_boundary_is_preserved(tmp_path):
    store = FeedbackSources(tmp_path / "memory", media_roots=[tmp_path / "cache"])
    result = store.capture(
        payload() | {"attachment_paths": [str(tmp_path / "private-config.json")]}
    )
    assert result["attachment_errors"] and result["attachments"] == []
    assert store.original(result["source_ref"]) == payload()["text"]


def test_same_message_new_enrichment_keeps_first_source_and_saves_attributed_supplement(tmp_path):
    store = FeedbackSources(tmp_path)
    first = store.capture(
        payload("First generated image reading")
        | {"input_attribution": "Derived Hermes image input"}
    )
    later = store.capture(
        payload("Regenerated image reading")
        | {"input_attribution": "Derived Hermes image input", "transcription": "IWM, not IVM"}
    )
    assert first["source_ref"] == later["source_ref"]
    assert store.original(first["source_ref"]) == "First generated image reading"
    context = store.context(later["supplement_sources"])
    assert "Regenerated image reading" in context and "IWM, not IVM" in context


async def test_unresolved_image_can_add_reading_before_corrected_preflight(tmp_path):
    config = cfg(tmp_path)
    first = await run_feedback(
        payload("Image input") | {"transformed_input": "Image description"}, config=config
    )
    memory = Capture({"text": "Saved source, no note write"})
    later = await run_feedback(
        payload("Image input") | {"action": "record", "transcription": "Accurate IWM255 reading"},
        config=config,
        deps=TeamDependencies(model_factory=lambda *_: memory),
    )
    assert first["status"] == "needs_context" and later["maintenance"]["completed"]
    packet = json.loads(memory.calls[0]["messages"][0]["content"][0]["text"])
    assert "Accurate IWM255 reading" in packet["originals"]["prior_owner_team_discussion"]["text"]


async def test_failed_manual_model_setup_has_private_actionable_diagnostic(tmp_path, monkeypatch):
    from portfolio_management.models import build_model

    config = cfg(tmp_path)
    model = config.model.model_copy(
        update={
            "provider": "openai",
            "endpoint": "http://127.0.0.1:8000/v1",
            "api_key_env": "TEST_FEEDBACK_LOCAL_KEY",
            "allow_inference_network": True,
        }
    )
    monkeypatch.delenv("TEST_FEEDBACK_LOCAL_KEY", raising=False)

    def factory(*_):
        return build_model(model)[0]

    result = await run_team(
        TeamRequest(question="Existing parent explanation", mode="education"),
        config=config,
        deps=TeamDependencies(model_factory=factory),
    )
    failure = json.loads(Path(result.artifacts["failure"]).read_text())
    assert (
        failure["category"] == "ValueError"
        and "API-key environment reference" in failure["message"]
    )
    assert failure["frames"] and all("locals" not in f for f in failure["frames"])
    monkeypatch.setenv("TEST_FEEDBACK_LOCAL_KEY", "local-no-auth")
    _, client = build_model(model)
    await client.close()
