"""Identical council policies shared by authors; role selection stays in role modules."""

from typing import Any

from wheel_team.config import TeamConfig
from wheel_team.types import RoleContext

READ_TOOLS = frozenset({"finance_calculate", "knowledge_read", "evidence_read", "evidence_find"})

DIRECT_TEXT_CONTRACT = {
    "mode": "direct_text",
    "advisory": (
        "Finish with ordinary assistant final text. This runtime instruction "
        "supersedes older typed output wording, including TeamResult formatting. "
        "No candidate, portfolio or reference metadata is required for delivery. "
        "Preserve exact citations, sources, units and limitations. Any supplied "
        "portfolio and original analyst reports arrive as caller task data; no "
        "publication or reader call is required."
    ),
}

COUNCIL_ADVISORY = (
    "Finish with ordinary authored final text. Preserve material findings, "
    "citations and qualifications. Any supplied private owner/account "
    "originals arrive as caller task data; peers retrieve relevant available "
    "originals by reference. No publication is required."
)

AUTHOR_HANDOFF = (
    "You are the author: finish with a concise useful final answer to the "
    "assignment, material findings, uncertainty and selected citations. Omit "
    "research history, source dumps, evidence inventories or hidden "
    "reasoning. Your answer is preserved exactly, never silently shortened. "
    "Child tools return final-result views and stable input refs. Use "
    "evidence_read for a bounded view of a selected envelope or source; "
    "large views are labeled incomplete excerpts. Select relevant refs for "
    "fact_check. Completion describes execution, not correctness."
)

FACT_CHECK_CONTRACT = (
    "Before returning factual claims, use fact_check(message, "
    "evidence_refs=[], assumptions=[]) to obtain reasoning feedback. Select "
    "only relevant original refs; nothing else is attached. Repair material "
    "errors; recheck materially changed claims when useful. At most three "
    "calls. Completed means execution, not factual approval. Preserve "
    "conditionality and disclose missing evidence. The checker cannot fetch "
    "sources. Expert may explicitly select input:portfolio, "
    "input:owner_context, or supplied input:continuity:N (the exact "
    "attributed private brief, not underlying notes). Private authors may "
    "select input:analyst:N, input:explorer:N, input:asymmetric:N and "
    "input:specialist:N for the numbered original report envelopes supplied "
    "in this invocation. Specialist originals retain author identity and "
    "outcome; they are judgments."
)

PRIVATE_REFERENCE_CONTEXT = (
    "Your assignment is supplied separately. Bounded views of private "
    "originals are available on demand through evidence_read(record_ref=...) "
    "and fact_check refs: input:portfolio, input:owner_context, supplied "
    "input:continuity:N, input:analyst:N, input:explorer:N, "
    "input:asymmetric:N and your own input:specialist:N. Request only "
    "relevant originals; retain dates and uncertainty. No account facts are "
    "inferred from missing inline context."
)


def specialist_tools(config: TeamConfig, base_tools: frozenset[str]) -> frozenset[str]:
    return base_tools | {"option_scenarios"} if config.council_enabled else base_tools


def specialist_context(context: RoleContext, run_data: dict[str, Any]) -> None:
    if context.config.council_enabled:
        run_data["private_context"] = PRIVATE_REFERENCE_CONTEXT
