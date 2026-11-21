"""Source-grounded knowledge manifests; no production content is authored here."""

from datetime import date
from typing import Literal, Self

from pydantic import Field, model_validator

from .common import JsonValue, PublicModel, QuantityValue, RecordId


class KnowledgeSource(PublicModel):
    source_id: RecordId
    title: str
    url: str | None = None
    source_date: date | None = None
    accessed_date: date | None = None
    content_hash: RecordId | None = None


class AuthoringProvenance(PublicModel):
    author: RecordId
    model_id: RecordId | None = None
    reasoning_effort: str | None = None
    session_ref: RecordId | None = None
    authored_date: date | None = None

    @model_validator(mode="after")
    def meaningful_provenance(self) -> Self:
        if not self.author.strip():
            raise ValueError("author must not be blank")
        if self.reasoning_effort is not None and (
            self.model_id is None or not self.model_id.strip()
        ):
            raise ValueError("reasoning_effort requires model_id provenance")
        return self


class WorkedExample(PublicModel):
    example_id: RecordId
    classification: Literal["synthetic", "historical"]
    description: str
    calculation: RecordId
    inputs: dict[str, JsonValue]
    expected_outputs: dict[str, QuantityValue | JsonValue]
    independent_basis: str
    source_refs: tuple[RecordId, ...] = ()
    tolerance: str | None = None

    @model_validator(mode="after")
    def independently_grounded_example(self) -> Self:
        if not self.independent_basis.strip():
            raise ValueError("independent_basis must not be blank")
        if self.classification == "historical" and not self.source_refs:
            raise ValueError("historical examples require source_refs")
        return self


class KnowledgeModule(PublicModel):
    module_id: RecordId
    version: RecordId
    title: str
    path: RecordId
    content_hash: RecordId
    source_refs: tuple[RecordId, ...]
    claim_classification: Literal["sourced_fact", "derived", "illustrative", "judgment"]
    prerequisites: tuple[RecordId, ...] = ()
    claims: dict[str, str] = Field(default_factory=dict)
    worked_examples: tuple[WorkedExample, ...] = ()
    authoring_provenance: AuthoringProvenance


class KnowledgeManifest(PublicModel):
    manifest_id: RecordId
    version: RecordId
    status: Literal["development", "approved"]
    modules: tuple[KnowledgeModule, ...]
    sources: tuple[KnowledgeSource, ...]
    owner_approval_ref: RecordId | None = None
    content_hash: RecordId | None = None

    @model_validator(mode="after")
    def unique_ids_and_resolved_references(self) -> Self:
        module_ids = {module.module_id for module in self.modules}
        source_ids = {source.source_id for source in self.sources}
        if len(module_ids) != len(self.modules):
            raise ValueError("duplicate module IDs")
        if len(source_ids) != len(self.sources):
            raise ValueError("duplicate source IDs")
        for module in self.modules:
            if set(module.prerequisites) - module_ids:
                raise ValueError(f"unresolved prerequisite module in {module.module_id}")
            if set(module.source_refs) - source_ids:
                raise ValueError(f"unresolved source reference in module {module.module_id}")
            for example in module.worked_examples:
                if set(example.source_refs) - source_ids:
                    raise ValueError(f"unresolved source reference in example {example.example_id}")
        return self
