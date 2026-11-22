# Isolated agent evals v1

This is a small synthetic role pilot, not investment acceptance or a production team run. Four fictional worlds are reused across six isolated roles: Explorer, Analyst, Expert (`wheel_expert`), Lead (`team_lead`), Checker (`fact_checker`), and Hermes presentation (`hermes_presentation`). Names and numbers are fictional. No real market, account, search, email, or order tool runs.

Native roles use the deployed PM6 authored prompts, caller amendments, production SDK role constructors and exact advertised tool schemas captured from that deployment. Native final output stays ordinary text. Delegation, checker-return, market, evidence and knowledge tools are controlled fixtures keyed by meaningful request arguments, without a required call order or phrase trajectory. The standalone Checker uses the actual checker authored prompt and zero-tool scope; other roles receive mocked checker feedback. Variants include scoped confirmation, accurate correction, empty feedback and an erroneous objection. Calculator responses are deliberately unavailable in this pilot and recorded as **mock coverage**, not proof of bad arithmetic or a required calculator call.

Hermes is a **presentation-role simulation with the same eval model**, not the actual Hermes runtime or host/email test. Its source editorial instructions retain their meaning; private operational paths are replaced with placeholders before freezing. An explicit adapter supplies the original report and prepared brief context and requests the separate brief JSON as final text. Mock acknowledgement never means actual email acceptance.

## Commands

Run from the repository root with the locked Python3.12 environment (`uv sync --group dev` for setup). Offline validation uses no inference:

```bash
uv run python -m portfolio_management.evaluation.agent_suite --validate
uv run pytest tests/thin/test_agent_eval_suite.py tests/thin/test_role_isolation.py tests/thin/test_eval_modes.py -q
uv run python -m portfolio_management.evaluation.agent_suite --role fact_checker --output /tmp/agent-eval-dry-unique --dry-run
```

Single-command live suite (explicitly authorized inference only; local eval server, fresh output path):

```bash
WHEEL_EVAL_API_KEY=local-no-auth uv run python -m portfolio_management.evaluation.agent_suite --output /tmp/agent-eval-pilot-unique --concurrency 2 --stop-before 2026-11-04T16:30:00+02:00
```

Select one or more roles with repeated `--role`, or cases with repeated `--case` (e.g. `--case wheel_expert-coral`). `--suite` selects a frozen bundle. The output path must not exist. Maximum24 selected cases, at most2 outstanding case requests, no automatic retries. Generation controls match the current deployment: qwen,65536 output tokens, low reasoning effort, reasoning excluded, default temperature; Explorer1.1. A300-second per-case wall deadline is an operational evaluation limit, not a claim that a truncated answer is semantically wrong.

Before live inference, the operator must obtain exact frozen-package release, inspect current server idle/process state and the next portfolio cron job. `--stop-before` prevents dispatch when the remaining interval is shorter than the full case deadline plus60seconds. Deferred cases have `attempted:false`, never agent-failure grades. Run unfinished selected cases later only under the same24-attempt allocation after the portfolio job has completed and idle is verified. The runner does not modify cron or the server. Do not run a live batch through the dry-run command or use dry-run as authorization.

## Evidence and review

Each case saves ordinary final text, model-generation counts and provider token usage when supplied, latency, separate scenario-time and real wall-time events, exact ordinary tool arguments/results, setup errors, timeouts and mock-coverage limitations. No raw reasoning, credentials or raw transport responses are retained. Partial ordinary text survives invocation failure. `attempted` is based on a model-generation-start event; a setup failure is a harness error.

`cases.json` contains only agent-visible questions and synthetic fixtures. Private expected facts and root grading criteria remain outside this bundle and are never loaded by the runner. Independent root review grades every ordinary output; this runner performs no financial semantic scoring, fixed-five-candidate check, exact phrase check or forced calculator/tool trajectory. Review factual reasoning, fidelity, source limits and mock compatibility separately. One stochastic24-case pilot over four reused worlds is limited evidence, not a reliability benchmark.

`bundle-manifest.json` freezes the public data bundle. The campaign artifact manifest additionally binds runner code, tests and documentation. Post-review publication is allowlisted and excludes owner outputs, real holdings, private grading files, provider credentials, integration state and request transports.
