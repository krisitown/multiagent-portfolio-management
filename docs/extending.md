# Add an expert, tool or prompt

## Add a council expert

This runtime uses explicit role contracts rather than automatic plugin discovery. Adding an expert is an intentional code change:

1. Add a small `agents/your_expert.py` constructor accepting `RoleContext`, following `agents/buffett.py` and calling `build_role`.
2. Extend `RoleName` and `SpecialistRequest.role` in `types.py`, relevant `AgentRole`/prompt types, and `TeamConfig.role_models` if independently routed.
3. Add the role to `ROLE_TOOLS` and the appropriate private/checked-author sets in `agents/base.py`. Grant only the tools it needs.
4. Register its constructor and delegation/routing handling in `team.py`. Update the manager's advertised specialist tool schema and prompt instructions together.
5. Add a Markdown role prompt and its SHA-256 entry in a selected manifest. Caller amendments must bind that role's actual baseline hash.
6. Add synthetic tests analogous to `tests/thin/test_portfolio_council.py`: explicit delegation, separate model routing, permitted/forbidden tools, failure propagation and specialist evidence returned to the manager.

Do not copy a persona's output into another role or mark its recommendation as owner policy. Council disagreements are evidence for the manager to resolve, not votes establishing correctness.

## Add a tool

Implement a bounded function in `tools/` with a precise schema, then bind it in `team.py` and add it only to the intended roles' allowlist. Return typed `ToolResult` states with reasons, evidence references, provenance and honest unavailable/partial states. For financial calculations use decimal arithmetic in `domain/`; do not trust model arithmetic or a checker correction without recomputation.

Test malformed arguments, missing values, units/currency/signs, provider failures, cancellation and evidence retention. A tool invocation's success does not establish source truth. Retrieved pages cannot alter tool permissions or role instructions.

## Update prompts

Each manifest lists role, shared and module Markdown paths plus hashes. Knowledge has an independent indexed manifest. `prompt_loader.py` and `knowledge_loader.py` reject changed or unsafe paths. Update the affected SHA-256 and caller-amendment baseline after editing. Validate with:

```bash
uv run python scripts/validate_bundles.py
```

Avoid user/account defaults in generic prompts. Put actual preferences, capital, allowed leverage and dated constraints in the external application's input. Worked examples must be fictional and clearly labelled.
