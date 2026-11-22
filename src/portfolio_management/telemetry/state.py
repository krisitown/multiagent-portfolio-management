"""Deterministic run projection; measurements retain source scope and missing values."""

import json
from datetime import datetime
from typing import Any


def nested_statuses(value: Any, depth: int = 0) -> list[str]:
    if depth > 30:
        return []
    found = set()
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return nested_statuses(json.loads(value), depth + 1)
        except (ValueError, RecursionError):
            return []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("status", "outcome", "error_category") and isinstance(item, str):
                found.add(item)
            if key in ("truncated", "blocked", "unavailable", "extraction_failed") and item is True:
                found.add(key)
            if key == "error" and item:
                found.add("error")
            found.update(nested_statuses(item, depth + 1))
    if isinstance(value, list):
        for item in value:
            found.update(nested_statuses(item, depth + 1))
    return sorted(found)


def payload(value: Any) -> Any:
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        value = value[0].get("text", value)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def timestamp(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


def completed_public_text(lane: dict[str, Any], text: str) -> bool:
    return any(g.get("text_final") and g.get("text") == text for g in lane["generations"].values())


def completed_output_tool(lane: dict[str, Any], text: str) -> bool:
    name, fields = (
        ("ExpertResult", ("assessment",))
        if lane["role"] == "wheel_expert"
        else ("TeamResult", ("report_markdown", "summary"))
    )
    for tool in lane["tools"].values():
        if tool.get("name") != name or tool.get("status") != "success":
            continue
        inputs = tool.get("inputs") or {}
        if next((inputs.get(field) for field in fields if inputs.get(field)), None) == text:
            return True
    return False


def terminal_narrative(lane: dict[str, Any]) -> bool:
    generations = list(lane["generations"].values())
    return bool(
        generations
        and generations[-1].get("status") == "success"
        and generations[-1].get("stop_reason") in ("end_turn", "max_tokens")
    )


class RunState:
    def __init__(self) -> None:
        self.invocations: dict[str, dict[str, Any]] = {}
        self.manifest: dict[str, Any] = {}
        self.report: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self.research: list[dict[str, Any]] = []
        self.experts: list[dict[str, Any]] = []
        self.completed: str | None = None
        self.started_at: str | None = None
        self.completed_at: str | None = None
        self.peak = 0
        self.health: dict[str, Any] = {}
        self.event_count = 0

    def lane(self, identity: str, role: str = "") -> dict[str, Any]:
        if identity not in self.invocations:
            self.invocations[identity] = {
                "id": identity,
                "role": role,
                "status": "observed",
                "generations": {},
                "tools": {},
                "output": "",
                "output_provenance": "unavailable",
                "output_kind": "unattributed",
                "output_label": "Saved output · origin unverified",
                "task": "",
                "handoff_chars": None,
                "parent_call_id": None,
            }
        return self.invocations[identity]

    def apply(self, events: list[dict[str, Any]]) -> None:
        for e in events:
            kind = e.get("event")
            if not isinstance(kind, str):
                continue
            self.event_count += 1
            if kind.startswith("artifact_"):
                if kind == "artifact_manifest":
                    self.manifest = e["results"]
                elif kind == "artifact_research":
                    self.research = e["results"]
                elif kind == "artifact_expert":
                    self.experts = e["results"]
                elif kind == "artifact_report":
                    self.report = e
                continue
            self.events.append(e)
            self.events = self.events[-2000:]
            if kind == "run_started":
                self.started_at = e.get("at")
            if kind in ("run_completed", "run_failed"):
                self.completed = e.get("completion_reason", "failed")
                self.completed_at = e.get("at")
            if kind == "observer_health":
                self.health[e.get("invocation_id", "unknown")] = e
            identity = e.get("invocation_id")
            if not isinstance(identity, str):
                continue
            lane = self.lane(identity, e.get("role", ""))
            lane["last_activity"] = e.get("at")
            if kind == "invocation_started":
                lane["handoff_chars"] = e.get("prompt_chars")
                lane["handoff_provenance"] = "measured invocation input text characters"
            if kind in ("model_generation_started", "model_generation_finished", "public_text"):
                number = e.get("generation")
                generation = lane["generations"].setdefault(
                    number, {"generation": number, "text": ""}
                )
                if kind == "model_generation_started":
                    generation.update(started_at=e.get("at"), status="generating/waiting")
                    lane.setdefault("started_at", e.get("at"))
                elif kind == "model_generation_finished":
                    generation.update(
                        {
                            k: e.get(k)
                            for k in (
                                "elapsed_seconds",
                                "usage",
                                "stop_reason",
                                "status",
                                "error_category",
                            )
                        }
                    )
                    generation["completed_at"] = e.get("at")
                else:
                    sequence = e.get("sequence")
                    if sequence is not None and sequence <= generation.get("sequence", -1):
                        continue
                    if sequence is not None:
                        generation["sequence"] = sequence
                    if e.get("final") or e.get("replace"):
                        generation["text"] = e.get("text", "")
                        generation["text_final"] = bool(e.get("final"))
                    elif not generation.get("text_final"):
                        generation["text"] += e.get("text", "")
            if kind in ("tool_call_started", "tool_call_finished"):
                call = e.get("call_id")
                tool = lane["tools"].setdefault(call, {"call_id": call})
                if kind == "tool_call_started":
                    tool.update(
                        name=e.get("name"),
                        inputs=e.get("inputs"),
                        started_at=e.get("at"),
                        status="waiting",
                    )
                    tool["input_chars"] = len(json.dumps(e.get("inputs"), ensure_ascii=False))
                else:
                    tool.update(
                        {
                            k: e.get(k)
                            for k in (
                                "name",
                                "status",
                                "result",
                                "elapsed_seconds",
                                "error_category",
                            )
                        }
                    )
                    tool["completed_at"] = e.get("at")
                    tool["nested_statuses"] = nested_statuses(e.get("result"))
            active = sum(
                g.get("status") == "generating/waiting"
                for i in self.invocations.values()
                for g in i["generations"].values()
            )
            self.peak = max(self.peak, active)
        self._reconcile()

    def _reconcile(self) -> None:
        # Jobs have no invocation-start event in old traces. Unique job prefix is reliable;
        # repeated/ambiguous IDs intentionally do not inherit another invocation's report.
        lead_tools = [t for i in self.invocations.values() for t in i["tools"].values()]
        for t in lead_tools:
            if t.get("name") == "research_batch":
                inputs = t.get("inputs") or {}
                for job in inputs.get("jobs", []):
                    if not isinstance(job, dict):
                        continue
                    matches = [
                        i
                        for i in self.invocations.values()
                        if i["id"].startswith("analyst:" + str(job.get("job_id")) + ":")
                    ]
                    if len(matches) == 1:
                        matches[0].update(task=job.get("question", ""), parent_call_id=t["call_id"])
            if t.get("name") == "consult_expert":
                start, end = timestamp(t.get("started_at")), timestamp(t.get("completed_at"))
                if start is None:
                    continue
                matches = [
                    i
                    for i in self.invocations.values()
                    if i["role"] == "wheel_expert"
                    and (invocation_start := timestamp(i.get("started_at"))) is not None
                    and invocation_start >= start
                    and (end is None or invocation_start <= end)
                ]
                inputs = t.get("inputs") or {}
                for match in matches:
                    match["correlation"] = (
                        "recorded UTC interval; unique"
                        if len(matches) == 1
                        else "ambiguous consultation interval"
                    )
                if len(matches) == 1:
                    match = matches[0]
                    match.update(
                        parent_call_id=t["call_id"],
                        task=inputs.get("question", ""),
                        handoff_question_chars=len(inputs.get("question", "")),
                    )
                    if match.get("handoff_chars") is None:
                        match["handoff_provenance"] = "full handoff unavailable in legacy trace"
                    result = payload(t.get("result"))
                    if isinstance(result, dict) and isinstance(result.get("assessment"), str):
                        failure_notice = bool(result.get("error")) and not (
                            completed_public_text(match, result["assessment"])
                            or completed_output_tool(match, result["assessment"])
                            or terminal_narrative(match)
                        )
                        match.update(
                            output=result["assessment"],
                            output_kind="runtime_fallback" if failure_notice else "ordinary",
                            output_label="Runtime failure notice"
                            if failure_notice
                            else "Returned ordinary output",
                            output_provenance=(
                                "runtime-generated consult_expert failure notice"
                                if failure_notice
                                else "exact consult_expert returned assessment"
                            ),
                            status=result.get(
                                "outcome", "failed" if result.get("error") else "success"
                            ),
                        )
        for result in self.research:
            if not isinstance(result, dict):
                continue
            matches = [
                i
                for i in self.invocations.values()
                if i["id"].startswith("analyst:" + str(result.get("job_id")) + ":")
            ]
            duplicate = sum(r.get("job_id") == result.get("job_id") for r in self.research) > 1
            if len(matches) == 1 and not duplicate:
                matches[0].update(
                    output=result.get("summary", ""),
                    status=result.get("outcome", "unknown"),
                    output_provenance="exact research-results summary",
                    output_kind="ordinary",
                    output_label="Returned ordinary output",
                )
        for lane in self.invocations.values():
            generations = list(lane["generations"].values())
            if self.completed:
                for g in generations:
                    if g.get("status") == "generating/waiting":
                        g["status"] = "incomplete"
            if lane["id"] == "lead" and self.report:
                text = self.report["text"]
                # Captured completed public text wins even if native run status failed.
                # Otherwise a failed/nonterminal lead cannot author the saved final report.
                observed = completed_public_text(lane, text) or completed_output_tool(lane, text)
                host_shape = text.startswith("# Team response\n") and any(
                    marker in text
                    for marker in (
                        "Run stopped before final synthesis.",
                        "Run cancelled before final synthesis.",
                        "Run deadline reached before final synthesis.",
                    )
                )
                native_status = self.completed or self.manifest.get("completion_reason")
                if observed or (terminal_narrative(lane) and not host_shape):
                    kind, label = "ordinary", "Returned ordinary output"
                elif native_status and native_status != "complete":
                    kind, label = "runtime_fallback", "Runtime fallback report"
                else:
                    kind, label = "unattributed", "Report artifact · origin unverified"
                lane.update(
                    output_kind=kind,
                    output_label=label,
                    output=self.report["text"],
                    output_provenance=(
                        "runtime-generated report.md fallback; not lead prose"
                        if kind == "runtime_fallback"
                        else "report.md presentation copy"
                    ),
                    status=self.completed
                    or self.manifest.get("completion_reason", "final report saved"),
                )
            elif lane["output_provenance"] == "unavailable":
                if any(g.get("status") == "generating/waiting" for g in generations):
                    lane["status"] = "generating/waiting"
                elif (
                    any(t.get("status") == "waiting" for t in lane["tools"].values())
                    and not self.completed
                ):
                    lane["status"] = "waiting on tools/batch"
                else:
                    lane["status"] = "incomplete" if self.completed else "between generations"

    def snapshot(self) -> dict[str, Any]:
        lanes = []
        all_generations = []
        for lane in self.invocations.values():
            generations = list(lane["generations"].values())
            all_generations.extend(generations)
            tools = list(lane["tools"].values())
            lanes.append(
                {
                    **lane,
                    "generations": generations,
                    "tools": tools,
                    "output_chars": len(lane["output"]),
                    "output_words": len(lane["output"].split()),
                }
            )
        known = [g for g in all_generations if g.get("usage")]
        tools = [t for i in lanes for t in i["tools"]]
        inputs = [g["usage"]["inputTokens"] for g in known if "inputTokens" in g["usage"]]
        outputs = [g["usage"]["outputTokens"] for g in known if "outputTokens" in g["usage"]]
        unclosed = sum("started_at" in g and "completed_at" not in g for g in all_generations)
        lifecycle_complete = bool(all_generations) and all(
            "started_at" in g and "completed_at" in g for g in all_generations
        )
        return {
            "manifest": self.manifest,
            "started_at": self.started_at or self.manifest.get("started_at"),
            "completed_at": self.completed_at or self.manifest.get("completed_at"),
            "completion_reason": self.completed or self.manifest.get("completion_reason"),
            "invocations": lanes,
            "report": self.report,
            "events": self.events,
            "observer_health": list(self.health.values()),
            "totals": {
                "invocations": len(lanes),
                "generations": len(all_generations),
                "unclosed_model_starts": unclosed,
                "model_lifecycle_complete": lifecycle_complete,
                "concurrency_scope": (
                    "SDK hook intervals; includes waiting; not server request concurrency"
                ),
                "waiting_tools": sum(t.get("status") == "waiting" for t in tools)
                if not self.completed
                else 0,
                "peak_concurrency": self.peak if lifecycle_complete else None,
                "known_usage_calls": len(known),
                "unknown_usage_calls": len(all_generations) - len(known),
                "known_input_tokens": sum(inputs) if inputs else None,
                "known_output_tokens": sum(outputs) if outputs else None,
                "peak_prompt_tokens": max(
                    (g["usage"]["inputTokens"] for g in known if "inputTokens" in g["usage"]),
                    default=None,
                ),
                "tool_calls": len(tools),
                "transport_failures": sum(
                    t.get("status") not in ("success", "waiting", None) for t in tools
                ),
                "nested_affected_calls": sum(
                    any(
                        s not in ("success", "complete", "available")
                        for s in t.get("nested_statuses", [])
                    )
                    for t in tools
                ),
            },
        }
