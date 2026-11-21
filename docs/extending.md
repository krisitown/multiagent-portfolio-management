# Add an expert, tool or prompt

## Add a council expert

This runtime uses explicit role contracts rather than automatic plugin discovery. Adding an expert is an intentional code change:

1. Add `agents/your_expert.py` with a `RoleDefinition` and constructor accepting `RoleContext`, following `agents/buffett.py`. The constructor passes its definition to `base.build_agent`.
2. Extend `RoleName` and `SpecialistRequest.role` in `types.py`, relevant `AgentRole`/prompt types, `models.RunHooks` output routing, and `TeamConfig.role_models` if independently routed.
3. Declare its base tools, output type, private-context policy and checker eligibility in that module. Optional callbacks adjust permissions from the supplied base tools, add contract/context details or install middleware. Add the module to `agents/registry.py`; tool inventories and private/checked-author sets are derived from definitions. Continuity remains outside the public evaluation inventory. Grant only the tools it needs.
4. Register its constructor and delegation/routing handling in `team.py`. Update the manager's advertised specialist tool schema and prompt instructions together.
5. Add a Markdown role prompt and its SHA-256 entry in a selected manifest. Caller amendments must bind that role's actual baseline hash.
6. Add synthetic tests analogous to `tests/thin/test_portfolio_council.py`: explicit delegation, separate model routing, permitted/forbidden tools, failure propagation and specialist evidence returned to the manager.

Do not copy a persona's output into another role or mark its recommendation as owner policy. Council disagreements are evidence for the manager to resolve, not votes establishing correctness.

`base.py` owns SDK construction, generic context rendering, hooks and conversation isolation. Role-specific behavior belongs in the role module: for example, lead handoff and synthesis reserves live in `team_lead.py`, manager planning in `portfolio_manager.py`, and continuity's direct-text contract in `continuity.py`. Shared council text and research middleware are reused without dispatching on role names. Existing `base.build_role`, `base.role_tools` and inventory imports remain compatibility entry points.

For refactors, `tests/unit/test_agent_construction.py` checks 256 pre-refactor synthetic construction signatures across roles and configuration switches. It covers the exact prompts, caller messages, tool schemas and middleware order; do not regenerate the baseline merely to hide a changed policy. Dynamic middleware and private-data boundaries also have SDK integration tests.

## Add a tool

Implement a bounded function in `tools/` with a precise schema, then bind it in `team.py` and add it only to the intended roles' allowlist. Return typed `ToolResult` states with reasons, evidence references, provenance and honest unavailable/partial states. For financial calculations use decimal arithmetic in `domain/`; do not trust model arithmetic or a checker correction without recomputation.

Test malformed arguments, missing values, units/currency/signs, provider failures, cancellation and evidence retention. A tool invocation's success does not establish source truth. Retrieved pages cannot alter tool permissions or role instructions.

## Update prompts

Each manifest lists role, shared and module Markdown paths plus hashes. Knowledge has an independent indexed manifest. `prompt_loader.py` and `knowledge_loader.py` reject changed or unsafe paths. Update the affected SHA-256 and caller-amendment baseline after editing. Validate with:

```bash
uv run python scripts/validate_bundles.py
```

Avoid user/account defaults in generic prompts. Put actual preferences, capital, allowed leverage and dated constraints in the external application's input. Worked examples must be fictional and clearly labelled.
