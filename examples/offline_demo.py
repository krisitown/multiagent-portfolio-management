"""Network-free scripted plumbing demo; this is not a financial/model evaluation."""
import asyncio
import os
from pathlib import Path

from portfolio_management.config import ModelConfig, PromptSet, TeamConfig
from portfolio_management.team import run_team
from portfolio_management.types import TeamDependencies, TeamRequest


async def main():
    config = TeamConfig(
        model=ModelConfig(provider="scripted", model_id="offline"),
        prompts=PromptSet.development(),
        child_output_mode="typed",
        output_dir=Path(os.environ.get("RUN_OUTPUT_DIR", "runs/offline")),
    )
    result = await run_team(
        TeamRequest(question="Explain option assignment using fictional examples.", mode="education"),
        config=config,
        deps=TeamDependencies(),
    )
    print(f"Scripted demo: {result.completion_reason}; report: {result.artifacts['report']}")
    assert result.completion_reason == "complete"


if __name__ == "__main__":
    asyncio.run(main())
