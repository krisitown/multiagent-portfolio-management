# Validation and limitations

## Current full-suite audit

The complete default `pytest -q` run passes: **1,196 passed, 14 skipped, zero failures**, without marker filtering. The existing skips require explicitly selected private archived artifacts that are not distributed in this public repository; no new skips, xfails or deselections were introduced. CI now runs this full suite and exposes failures in annotations.

All 19 inherited failures were audited. CLI tests now read narrative stdout and status/error JSON stderr while checking both channels; SDK fixtures explicitly select their typed/structured contracts; option fixtures use their declared source date and test precise invalid-data paths. Structured rendering now explicitly opts into integrity validation and bounded evidence projections, including stopped-run research preservation. Authored text remains the unchanged default. Existing assertions were retained, with additional configuration, evidence-coverage and output-channel assertions.

Prompt bundles, offline demo, package builds and touched-code lint checks pass. These are offline implementation checks, not new model benchmark or investment-quality evidence.

## Initial public release

This is a source release of an experimentally deployed runtime, not a claim of production financial reliability. It preserves the working orchestration and adds portable configuration, documentation and integration instructions. Private host integrations, delivery scripts, original account data and raw report history are excluded.

Validation performed on Python 3.13:

- Public prompt loader: 9 role prompts and 30 indexed knowledge modules verified, including caller baseline hashes.
- Unit and contract suites: **347 passed, 9 skipped**. Public JSON schemas were regenerated from the shipped validated models.
- Council, direct-text handoff, continuity and completion-observer suites: **59 passed**.
- Fact-check team suite: **5 passed**.
- Scripted network-free demo: completed and wrote inspectable result/report artifacts.
- Source and wheel distributions: built successfully.

The broad unit/contracts/thin run initially produced 1,126 passes, 20 failures and 14 skips. One failure was fixed by regenerating the public schema export. The remaining 19 inherited failures were subsequently corrected in the full-suite audit above; the historical counts below describe verification at those earlier releases.

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
