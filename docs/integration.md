# Integrate from your own agent or application

## Stable host boundary

Your host obtains a dated portfolio and a question, then invokes the runtime once. It owns authentication, consent, scheduling and presentation. The runtime owns research orchestration and saved results. An external agent can be Hermes, a web UI, a shell script or another SDK application.

```python
import json
import subprocess
from pathlib import Path

completed = subprocess.run([
    "wheel-team", "ask", "--mode", "combined",
    "--question", "Review this portfolio for income and long-term wealth.",
    "--portfolio-text", str(Path("private/portfolio.txt").resolve()),
    "--config", str(Path("private/team-config.json").resolve()),
    "--output", str(Path("private/runs").resolve()),
], check=False, capture_output=True, text=True)
# Read the newly created run's result.json/report.md and inspect completion_reason.
# Keep the original report unchanged; do not treat exit code as financial approval.
```

Use an argument list, never interpolate model/owner text into a shell command. `--portfolio` accepts the validated `PortfolioSnapshot` JSON contract; `--portfolio-text` accepts exact readable text. They are mutually exclusive. An optional `--owner-context` file adds explicitly supplied preferences. Use `--parent-review` with `--review-kind targeted|refresh|explanation` for follow-ups.

## Python embedding

Use `wheel_team.cli.load_team_config`, `wheel_team.types.TeamRequest`, `TeamDependencies` and async `wheel_team.team.run_team`. Inspect their current signatures before embedding; the CLI is the simpler subprocess boundary. `TeamDependencies` permits explicitly injected market/research providers, clocks and models for controlled tests or custom applications.

## Research connector

The included `HermesResearchBackend` is an optional adapter to an existing Hermes public-web tool installation. It has no operator hostname or account default. It is not required to import or test the runtime. If you use it, supply all connector paths and credentials through your own environment/configuration.

To replace it, implement the small `describe()` / async `request(payload)` interface consumed by `tools/research.py`, then inject the backend through `TeamDependencies`. Search requests use `operation="search"`, `query`, `limit`; fetch requests use `operation="fetch"`, `urls`. Preserve the existing normalized result envelopes (URL/title/content, truncation, timestamps and hash scope). See `adapters/research.py` and `tests/thin/test_research.py` for argument-sensitive examples. Missing data must remain unavailable.

Market-provider implementations satisfy `adapters/market/base.py`; HTTP protocol examples and schemas are under `examples/market-provider/` and `schemas/`. Keep public queries free of portfolio quantities, account identifiers and private owner context.

## Outputs and telemetry

A run directory contains `result.json`, `report.md`, `manifest.json`, `trace.jsonl`, evidence and specialist artifacts. Failed/partial runs preserve available artifacts. Check `completion_reason` and warnings; an apparently complete but short answer can still be defective. Do not overwrite the original to make a failed run look successful.

```bash
wheel-team telemetry --run-dir ./runs/RUN_ID --port 8765
```

The viewer is intended for local/private use. Run artifacts can contain private portfolio context even when model reasoning is excluded from public-output capture. Your host controls access and retention. This release includes no email sender, broker credentials, account database or scheduler.
