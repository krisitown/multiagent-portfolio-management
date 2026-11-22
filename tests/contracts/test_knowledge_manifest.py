"""Knowledge handoff rejects broken references and ambiguous authoring metadata."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from portfolio_management.contracts import KnowledgeManifest

FIXTURE = Path(__file__).parents[1] / "fixtures" / "knowledge" / "valid_minimal"


def payload():
    return json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))


def test_minimal_development_manifest_preserves_synthetic_example_and_file_hashes():
    manifest = KnowledgeManifest.model_validate(payload())
    module = manifest.modules[0]
    assert manifest.status == "development"
    assert manifest.owner_approval_ref is None
    assert module.claim_classification == "illustrative"
    assert module.worked_examples[0].classification == "synthetic"
    assert module.worked_examples[0].expected_outputs == {"total": 5}
    assert module.content_hash == hashlib.sha256((FIXTURE / module.path).read_bytes()).hexdigest()
    assert (
        manifest.sources[0].content_hash
        == hashlib.sha256((FIXTURE / "source.md").read_bytes()).hexdigest()
    )
    assert KnowledgeManifest.model_validate_json(manifest.model_dump_json()) == manifest


@pytest.mark.parametrize("reference_location", ["module", "example"])
def test_manifest_rejects_missing_source_ids(reference_location):
    data = payload()
    target = data["modules"][0]
    if reference_location == "example":
        target = target["worked_examples"][0]
    target["source_refs"] = ["missing-source"]
    with pytest.raises(ValidationError, match="source"):
        KnowledgeManifest.model_validate(data)


def test_manifest_rejects_duplicate_module_ids():
    data = payload()
    data["modules"].append(data["modules"][0].copy())
    with pytest.raises(ValidationError, match="duplicate.*module"):
        KnowledgeManifest.model_validate(data)


def test_manifest_rejects_duplicate_source_ids():
    data = payload()
    data["sources"].append(data["sources"][0].copy())
    with pytest.raises(ValidationError, match="duplicate.*source"):
        KnowledgeManifest.model_validate(data)


def test_manifest_rejects_unlabeled_example():
    data = payload()
    del data["modules"][0]["worked_examples"][0]["classification"]
    with pytest.raises(ValidationError, match="classification"):
        KnowledgeManifest.model_validate(data)


def test_synthetic_example_can_appear_in_sourced_fact_module():
    data = payload()
    data["modules"][0]["claim_classification"] = "sourced_fact"
    assert KnowledgeManifest.model_validate(data).modules[0].claim_classification == "sourced_fact"


def test_historical_example_requires_source_reference():
    data = payload()
    data["modules"][0]["worked_examples"][0]["classification"] = "historical"
    with pytest.raises(ValidationError, match="source"):
        KnowledgeManifest.model_validate(data)


def test_historical_example_can_reference_manifest_source():
    data = payload()
    data["modules"][0]["worked_examples"][0]["classification"] = "historical"
    data["modules"][0]["worked_examples"][0]["source_refs"] = ["synthetic-note"]
    manifest = KnowledgeManifest.model_validate(data)
    assert manifest.modules[0].worked_examples[0].source_refs == ("synthetic-note",)


def test_manifest_rejects_missing_prerequisite_module():
    data = payload()
    data["modules"][0]["prerequisites"] = ["missing-module"]
    with pytest.raises(ValidationError, match="prerequisite"):
        KnowledgeManifest.model_validate(data)


def test_manifest_resolves_prerequisite_to_other_module():
    data = payload()
    prerequisite = {**data["modules"][0], "module_id": "synthetic-prerequisite"}
    data["modules"].append(prerequisite)
    data["modules"][0]["prerequisites"] = ["synthetic-prerequisite"]
    assert KnowledgeManifest.model_validate(data).modules[0].prerequisites == (
        "synthetic-prerequisite",
    )


def test_manifest_rejects_blank_independent_example_basis():
    data = payload()
    data["modules"][0]["worked_examples"][0]["independent_basis"] = "  "
    with pytest.raises(ValidationError, match="independent_basis"):
        KnowledgeManifest.model_validate(data)


def test_manifest_rejects_blank_author_provenance():
    data = payload()
    data["modules"][0]["authoring_provenance"]["author"] = "  "
    with pytest.raises(ValidationError, match="author"):
        KnowledgeManifest.model_validate(data)


def test_manifest_rejects_reasoning_effort_without_model_provenance():
    data = payload()
    data["modules"][0]["authoring_provenance"]["reasoning_effort"] = "ultra"
    with pytest.raises(ValidationError, match="model"):
        KnowledgeManifest.model_validate(data)


def test_model_provenance_does_not_require_private_session_reference():
    data = payload()
    data["modules"][0]["authoring_provenance"].update(
        model_id="scripted-development-model", reasoning_effort="test"
    )
    provenance = KnowledgeManifest.model_validate(data).modules[0].authoring_provenance
    assert provenance.model_id == "scripted-development-model"
    assert provenance.session_ref is None
