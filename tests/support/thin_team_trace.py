"""Run the saved synthetic config through the default SDK and actual direct tools.

Usage: uv run python -m tests.support.thin_team_trace --output /private/output
"""

import argparse
import asyncio
import json
from pathlib import Path

from portfolio_management.cli import load_team_config
from portfolio_management.team import run_team
from portfolio_management.types import TeamDependencies, TeamRequest


async def run_fixture(output: Path):
    case = json.loads(Path("evals/thin/delegation-script.json").read_text())
    config = load_team_config(Path("evals/thin/delegation-config.json")).model_copy(
        update={"output_dir": output},
    )
    return await run_team(
        TeamRequest(question=case["question"], mode=case["mode"]),
        config=config,
        deps=TeamDependencies(),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    result = asyncio.run(run_fixture(parser.parse_args().output))
    print(result.model_dump_json(indent=2))
