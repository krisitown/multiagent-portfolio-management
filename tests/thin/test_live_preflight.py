"""Offline SDK preflight; explicit private manifests select exact production bytes.

CI uses development resources. Set WHEEL_PREFLIGHT_PROMPT_MANIFEST,
WHEEL_PREFLIGHT_KNOWLEDGE_MANIFEST and WHEEL_PREFLIGHT_WEB_RESPONSE to check the
approved bundle with a previously saved connector response. No inference or web call.
"""

import hashlib
import json
import os
from collections import deque
from pathlib import Path

import pytest

from tests.support.thin_sdk import ScriptModel, output
from portfolio_management.adapters.research import HermesResearchBackend, ResearchBackendConfig
from portfolio_management.config import ModelConfig, PromptSet, TeamConfig
from portfolio_management.contracts import AgentRole
from portfolio_management.prompt_loader import load_prompt
from portfolio_management.team import run_team
from portfolio_management.types import TeamDependencies, TeamRequest

QUESTION = (
    "Research Microsoft (MSFT) business quality and balance-sheet evidence from official "
    "investor-relations material. Preserve reporting periods; do not invent option quotes "
    "or trade readiness."
)
JOB = {"job_id": "msft-business-quality", "question": QUESTION}
FINAL = "Microsoft source evidence returned; no trade candidate or current readiness is proposed."


def tool_values(messages):
    values = []
    for message in messages:
        for block in message.get("content", []):
            for content in block.get("toolResult", {}).get("content", []):
                if "json" in content:
                    values.append(content["json"])
                elif "text" in content:
                    try:
                        values.append(json.loads(content["text"]))
                    except ValueError:
                        pass
    return values


def refs_from_search(messages):
    return next(
        value["evidence_refs"]
        for value in reversed(tool_values(messages))
        if isinstance(value, dict) and value.get("evidence_refs")
    )


def final_from_batch(messages):
    batch = next(value for value in reversed(tool_values(messages)) if isinstance(value, list))
    assert batch[0]["job_id"] == JOB["job_id"]
    assert batch[0]["outcome"] == "partial"
    return output(
        "TeamResult",
        summary=FINAL,
        supporting_refs=batch[0]["evidence_refs"],
        warnings=["Historical reporting evidence; option terms and broker capacity absent."],
    )


class ProbeModel(ScriptModel):
    """Script only transport responses; real SDK schemas/tools/hooks remain active."""

    def __init__(self, role, *steps):
        super().__init__(*steps)
        self.role, self.prompt_hashes, self.contexts = role, [], []

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        prompt, context = system_prompt.rsplit("\n\nRun context (data):\n", 1)
        self.prompt_hashes.append(hashlib.sha256(prompt.encode()).hexdigest())
        self.contexts.append(json.loads(context))
        if self.steps and callable(self.steps[0]):
            self.steps[0] = self.steps[0](messages)
        async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
            yield event


@pytest.fixture
def preflight(tmp_path, request):
    selected = os.environ.get("WHEEL_PREFLIGHT_PROMPT_MANIFEST")
    knowledge = os.environ.get("WHEEL_PREFLIGHT_KNOWLEDGE_MANIFEST")
    source = os.environ.get("WHEEL_PREFLIGHT_WEB_RESPONSE")
    destination = os.environ.get("WHEEL_PREFLIGHT_OUTPUT")
    cfg = TeamConfig(
        model=ModelConfig(model_id="offline-sdk-preflight", provider="scripted"),
        prompts=PromptSet(manifest_path=Path(selected), mode="production")
        if selected
        else PromptSet.development(),
        knowledge_manifest_path=Path(knowledge) if knowledge else None,
        output_dir=Path(destination) / request.node.name if destination else tmp_path / "runs",
        max_generations=8,
    )
    raw = (
        Path(source).read_bytes()
        if source
        else json.dumps(
            {
                "success": True,
                "data": {
                    "web": [
                        {
                            "title": "Microsoft investor relations",
                            "url": "https://www.microsoft.com/en-us/investor/earnings/fy-2026-q3/balance-sheets",
                            "description": (
                                "Synthetic preflight source response; "
                                "no current quotation or capacity."
                            ),
                        }
                    ]
                },
            }
        ).encode()
    )
    calls = []

    async def transport(argv, stdin, cwd, max_bytes):
        payload = json.loads(stdin)
        calls.append({"operation": payload["operation"], "query": payload.get("query")})
        return raw

    backend = HermesResearchBackend(
        ResearchBackendConfig(
            python="unused-offline-python",
            cwd=str(tmp_path),
            env_file="unused-offline.env",
        ),
        transport=transport,
    )
    return (
        cfg,
        backend,
        calls,
        {
            "prompt_mode": cfg.prompts.mode,
            "external_source_mode": "saved_connector_capture" if source else "synthetic_response",
            "external_response_sha256": hashlib.sha256(raw).hexdigest(),
        },
    )


async def run_probe(preflight, models, *, max_generations=None):
    cfg, backend, calls, identities = preflight
    if max_generations is not None:
        cfg = cfg.model_copy(update={"max_generations": max_generations})
    queued = deque(models)
    result = await run_team(
        TeamRequest(question=QUESTION, mode="focused"),
        config=cfg,
        deps=TeamDependencies(model_factory=lambda *_: queued.popleft(), research_backend=backend),
    )
    manifest = json.loads(Path(result.artifacts["manifest"]).read_text())
    trace = [json.loads(line) for line in Path(result.artifacts["trace"]).read_text().splitlines()]
    for model in models:
        expected = load_prompt(AgentRole(model.role), cfg.prompts)
        expected_hash = hashlib.sha256(expected.content.encode()).hexdigest()
        assert all(observed == expected_hash for observed in model.prompt_hashes)
    sidecar = {
        **identities,
        "question": QUESTION,
        "model_server_calls": 0,
        "external_backend_calls": 0,
        "injected_source_requests": calls,
        "generations": manifest["generations"],
        "completion_reason": result.completion_reason,
        "observed_prompt_hashes": {model.role: model.prompt_hashes for model in models},
        "artifacts": result.artifacts,
        "final_schema_additional_properties": manifest["tool_specs"]["team_lead"]["TeamResult"][
            "inputSchema"
        ]["json"].get("additionalProperties"),
    }
    Path(result.artifacts["result"]).with_name("offline-preflight.json").write_text(
        json.dumps(sidecar, indent=2) + "\n"
    )
    return result, manifest, trace


async def test_real_company_batch_result_and_final_sdk_contract(preflight):
    lead = ProbeModel("team_lead", output("research_batch", jobs=[JOB]), final_from_batch)
    analyst = ProbeModel(
        "analyst",
        output("public_search", query="site:microsoft.com MSFT balance sheet", limit=3),
        lambda messages: output(
            "ResearchResult",
            job_id=JOB["job_id"],
            outcome="partial",
            summary="Historical Microsoft source retrieved; trade inputs absent.",
            evidence_refs=refs_from_search(messages),
        ),
    )
    result, manifest, trace = await run_probe(preflight, [lead, analyst])
    assert result.summary == FINAL
    assert result.completion_reason == "complete"
    assert len(result.supporting_records) >= 1
    assert manifest["generations"] == 4
    assert preflight[2] == [
        {"operation": "search", "query": "site:microsoft.com MSFT balance sheet"}
    ]
    assert "Historical reporting evidence" in result.report_markdown
    specs = manifest["tool_specs"]
    dispatch = specs["team_lead"]["research_batch"]["inputSchema"]["json"]
    assert dispatch["required"] == ["jobs"]
    assert set(dispatch["$defs"]["ResearchJob"]["required"]) == {"job_id", "question"}
    assert "budget" not in dispatch["properties"]
    analyst_output = specs["analyst"]["ResearchResult"]["inputSchema"]["json"]
    assert set(analyst_output["required"]) == {"job_id", "outcome"}
    final_output = specs["team_lead"]["TeamResult"]["inputSchema"]["json"]
    assert final_output["required"] == ["summary"]
    # The pinned SDK omits additionalProperties; the behavioral repair test below
    # verifies the actual strict Pydantic boundary instead of assuming schema flags.
    assert "public_search" not in specs["team_lead"]
    assert "research_batch" not in specs["analyst"]
    assert [event["name"] for event in trace if event["event"] == "tool_call_started"] == [
        "research_batch",
        "public_search",
        "ResearchResult",
        "TeamResult",
    ]


async def test_foreign_dispatch_budget_field_is_rejected_and_lead_can_recover(preflight):
    bad = {**JOB, "budget": {"max_model_calls": 10}}
    lead = ProbeModel(
        "team_lead",
        output("research_batch", jobs=[bad]),
        output("research_batch", jobs=[JOB]),
        output("TeamResult", summary=FINAL),
    )
    analyst = ProbeModel(
        "analyst", output("ResearchResult", job_id=JOB["job_id"], outcome="partial")
    )
    result, manifest, _ = await run_probe(preflight, [lead, analyst])
    assert result.summary == FINAL
    assert len(analyst.calls) == 1
    assert len(json.loads(Path(result.artifacts["research"]).read_text())) == 1
    assert manifest["generations"] == 4
    assert "budget" in json.dumps(lead.calls[1]["messages"])


async def test_malformed_analyst_result_repairs_within_shared_generation_budget(preflight):
    lead = ProbeModel(
        "team_lead", output("research_batch", jobs=[JOB]), output("TeamResult", summary=FINAL)
    )
    analyst = ProbeModel(
        "analyst",
        output("ResearchResult", job_id=JOB["job_id"]),
        output("ResearchResult", job_id=JOB["job_id"], outcome="partial"),
    )
    result, manifest, _ = await run_probe(preflight, [lead, analyst], max_generations=4)
    assert result.summary == FINAL
    assert len(analyst.calls) == 2
    assert manifest["generations"] == 4
    assert "outcome" in json.dumps(analyst.calls[1]["messages"])


async def test_malformed_final_output_is_repaired_without_changing_dispatch_contract(preflight):
    lead = ProbeModel(
        "team_lead",
        output("research_batch", jobs=[JOB]),
        output("TeamResult", summary=17, foreign_report_id="invalid"),
        output("TeamResult", summary=FINAL),
    )
    analyst = ProbeModel(
        "analyst", output("ResearchResult", job_id=JOB["job_id"], outcome="partial")
    )
    result, manifest, _ = await run_probe(preflight, [lead, analyst], max_generations=4)
    assert result.summary == FINAL
    assert len(lead.calls) == 3
    assert manifest["generations"] == 4
    assert "foreign_report_id" in json.dumps(lead.calls[-1]["messages"])


@pytest.mark.parametrize("cap", [1, 2])
async def test_dispatch_and_malformed_child_cannot_reset_generation_allowance(preflight, cap):
    lead = ProbeModel(
        "team_lead", output("research_batch", jobs=[JOB]), output("TeamResult", summary="Unreached")
    )
    analyst = ProbeModel(
        "analyst",
        output("ResearchResult", job_id=JOB["job_id"]),
        output("ResearchResult", job_id=JOB["job_id"], outcome="success"),
    )
    result, manifest, _ = await run_probe(preflight, [lead, analyst], max_generations=cap)
    assert result.completion_reason == "partial"
    assert manifest["generations"] == cap
    assert len(lead.calls) == 1
    assert len(analyst.calls) == cap - 1
    children = json.loads(Path(result.artifacts["research"]).read_text())
    assert children[0]["outcome"] == "budget_exhausted"
    assert "generation" in " ".join(result.warnings).lower()
