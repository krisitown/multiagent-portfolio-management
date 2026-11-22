# Configuration

`scripts/configure.py` reads operator environment variables, writes a private JSON configuration and validates it using `TeamConfig`. It never contacts a model, broker or data provider. It does not read `.env` automatically; load your chosen environment explicitly.

| Variable | Purpose |
|---|---|
| `MODEL_BASE_URL`, `MODEL_NAME` | OpenAI-compatible Chat Completions endpoint and served model name |
| `MODEL_API_KEY` | Secret read by the model client; config stores only the variable name |
| `MODEL_REASONING_EFFORT` | Requested effort; must be supported by your endpoint/template |
| `MODEL_MAX_OUTPUT_TOKENS` | Per-generation output limit, including reasoning where applicable |
| `RUN_DEADLINE_SECONDS`, `RUN_OUTPUT_DIR` | Shared run budget and private artifact location |
| `PORTFOLIO_PATH` | Input path used by the CLI example; never a repository account default |
| `MODEL_TOKENIZER_PATH` | Optional matching local tokenizer for exact checker context counting |
| `CONTINUITY_DIR` | Optional external, private file-memory root |
| `RESEARCH_PYTHON`, `RESEARCH_CWD`, `RESEARCH_ENV_FILE` | Optional pre-existing Hermes read-only public-web bridge; all three or none |

The generated file uses absolute paths for selected repository resources so it works from another working directory. Regenerate it after relocating the checkout. Keep private configurations outside Git.

## Models

The core `openai` provider uses Chat Completions and sends `max_tokens`. Compatibility depends on the server, not only its API shape. Some hosted OpenAI models require `max_completion_tokens` or the Responses API; supply an appropriate model adapter rather than assuming the core client is interchangeable. Set `role_models` for explicit per-role overrides of `portfolio_manager`, `wheel_expert`, `dalio` and `buffett`.

For the PortfolioFinQwen template, supported effort labels are `low`, `medium` and `xhigh`; `high` is rejected. Effort is a model instruction, not a hard thinking-token budget. Separate `enable_thinking` support depends on the template. Model cards contain deployment-specific notes.

## Fact checking and continuity

The generated configuration enables the final-report checker by default. A matching local tokenizer and the optional `transformers` package allow exact context counting. Without them, the runtime uses a conservative UTF-8 byte bound plus framing allowance, which can reduce usable context. Checking is author-directed and fallible; prompts cannot guarantee that the final draft was checked correctly.

Continuity is omitted unless an explicit root is provided. The runtime's memory tools operate on that external root. Your application is responsible for populating it with authorized, attributed context.

## Market data

The runtime supports captured fixtures and an optional configured market-provider transport. The historical `SparkMarketProvider` name refers to an adapter, not a requirement to use a particular machine. See its explicit `SSHResearchConfig` fields and `examples/market-provider/` protocol samples. Broker input and market-provider data are separate. Never infer current margin capacity from cash or substitute fetch time for the statement's valuation date.

## Report contracts

Authored report text is the default and passes through unchanged, including when `child_output_mode` is `typed`. To request the legacy structured renderer, set `structured_report: true` with `child_output_mode: typed` and council disabled. That renderer validates structured candidates and calculations, labels unsupported claims, projects bounded supporting records and marks integrity failures partial. Stopped typed runs retain completed child summaries and declared evidence projections. The original saved evidence remains unchanged.

The `ask` command writes report text to stdout and JSON status/artifact metadata (or input errors) to stderr. Integrations should parse the appropriate channel.
