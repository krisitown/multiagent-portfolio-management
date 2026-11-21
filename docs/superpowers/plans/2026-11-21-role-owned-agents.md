# Role-owned agent construction implementation plan

**Goal:** Move role policy from the shared constructor into each agent module while preserving the exact constructed prompts, messages, tools and middleware behavior.

**Architecture:** Each role exposes an immutable definition containing its output type, permissions and context/middleware hooks. `base.py` assembles an SDK Agent from that definition; an explicit registry preserves existing string-based entry points and inventories. Shared research and council helpers avoid duplicating identical policy text. No prompts, routing, budgets or deployment configuration change.

**Tech stack:** Existing Python, Strands, Pydantic and pytest.

## Constraints and review focus

- Preserve every existing role's permissions across council, asymmetric, continuity and checker switches.
- Preserve exact system prompts and caller messages, including private-data boundaries and follow-up context.
- Preserve middleware ordering, terminal tool selection and lead reserve transitions.
- Keep existing factories and base-module imports compatible; continuity stays outside the public evaluation inventory.
- Do not run paid inference or change any production deployment.

## Tasks

- [x] Capture baseline unit/contracts/thin/telemetry results and 256 synthetic construction signatures before modifying production code.
- [x] Add a failing test showing the shared builder consumes supplied role permissions without central policy dispatch.
- [x] Add `agents/definition.py`, `registry.py`, shared council/research policies and a continuity module; move each role's policy into its module. Keep base assembly generic and compatibility entry points intact.
- [x] Check all baseline signatures unchanged; run relevant agent, privacy, handoff, middleware, budget and telemetry tests. Compare full suite failure identities against baseline.
- [x] Update integration instructions and CI coverage, run prompt validation, demo, lint and build; obtain read-only code review and fix material findings before publication.

## Verification and rulings

The published release checkout was clean; work used a dedicated branch in that checkout. Production deployments remain outside this refactor's scope. All 256 signatures and 192 independently reviewed middleware scenarios matched. The final broad run retained exactly the baseline's 19 failure identities, with 1,173 passes versus the baseline's 1,168. The lead middleware's fake clock patch moved with the implementation; assertions did not change. Compatibility checks preserve the read-tool export and prompt/model validation precedence. New roles still require explicit lifecycle/output contracts, as documented in the extension guide.

## Model metadata correction

Separately remove the explicit timeout/error row from both model cards without changing benchmark scores or all-case denominators. Confirm logical parameter counts from tensor headers; correct the NVFP4 index's `metadata.total_parameters`, retaining its weight map and size. Publish only the cards and corrected index; verify weights unchanged and public size metadata refreshed.
