# Validation and limitations

## Initial public release

This is a source release of an experimentally deployed runtime, not a claim of production financial reliability. It preserves the working orchestration and adds portable configuration, documentation and integration instructions. Private host integrations, delivery scripts, original account data and raw report history are excluded.

Validation performed on Python 3.13:

- Public prompt loader: 9 role prompts and 30 indexed knowledge modules verified, including caller baseline hashes.
- Unit and contract suites: **347 passed, 9 skipped**. Public JSON schemas were regenerated from the shipped validated models.
- Council, direct-text handoff, continuity and completion-observer suites: **59 passed**.
- Fact-check team suite: **5 passed**.
- Scripted network-free demo: completed and wrote inspectable result/report artifacts.
- Source and wheel distributions: built successfully.

The broad unit/contracts/thin run initially produced 1,126 passes, 20 failures and 14 skips. One failure was the now-regenerated schema export. The other 19 failures were reproduced in the source checkout before release changes. They remain visible in the shipped tests rather than being suppressed: older JSON-on-stdout expectations, legacy typed final-output assumptions, two option-source fixture cases and partial-result/report preservation cases. These should be reconciled before treating the full regression suite as green. CI exercises the separately passing unit/contracts and current council paths; it does not certify the entire thin suite.

No paid inference, market-provider call or live brokerage action was used for release validation. Scripted tests establish plumbing behavior, not model competence.

## Role construction refactor

The refactor moves permissions, contracts, context policy and middleware into role modules while retaining shared SDK assembly and existing constructor/import entry points.

- All 256 synthetic construction signatures match the original implementation exactly: system prompts, caller messages, tool schemas, middleware order and SDK construction options.
- Independent review also compared 192 middleware scenarios, including terminal routing, time/generation reserves and emitted events; all matched the original.
- The broad offline unit/contracts/thin/telemetry baseline had **1,168 passed, 19 failed and 14 skipped**. The final refactor run had **1,173 passed, the same 19 failed and 14 skipped**; no new failure identities remained. The five added tests cover construction equivalence, definition-driven permissions and compatibility.
- One test's fake clock now also patches the module that owns the moved lead middleware; its assertions are unchanged. A separate 100 ms deadline test showed an intermittent summary-path race during verification; its focused and final runs passed.
- Prompt bundles, offline demo, lint and package builds passed. No inference or production deployment was used to validate this structural change.

## Practical limits

Package rename verification: the package, CLI and distribution now use `portfolio_management` / `portfolio-management`. Imports, resource paths, packaging, lockfile, examples, documentation and evaluation integrity hashes were updated. The broad offline suite had 1,174 passes, the same 19 baseline failures and 14 skips. The built wheel's imports and development prompt resources were also checked outside the source checkout.

- Prompt requirements for full reports, full-report checking and preserved corrections are instructions, not guarantees. Models can ignore them or return a short correction note that is mechanically marked complete.
- A checker is another fallible model. Verify action-driving arithmetic, signed quantities, quote sides, funding and contract mechanics against the original evidence.
- Research may repeat or consume the run budget. Explorer mode currently rejects role-phase reserves in configuration; do not assume the report has protected finalization time.
- Live public research needs a configured backend. The existing Hermes web bridge is optional; a different host can inject a compatible backend.
- Knowledge and prompts can contain dated references. They are not a live market feed. Cash and statement data do not establish current broker capacity.
- Preserve errors, caps, unavailable states and the exact original report. Never convert execution success into a financial approval badge.
