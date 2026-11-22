"""The built wheel must carry explicitly selected development prompt resources."""

import os
import subprocess
import sys
import sysconfig
import venv
from pathlib import Path


def test_wheel_prompt_loading_outside_repository(tmp_path):
    root = Path(__file__).resolve().parents[2]
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(tmp_path),
            str(root),
        ],
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    wheel = next(tmp_path.glob("*.whl"))
    environment = tmp_path / "installed"
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    installed = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stderr
    probe = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            f"import site; site.addsitedir({sysconfig.get_path('purelib')!r}); "
            "import portfolio_management; "
            "from portfolio_management.config import PromptSet; "
            "from portfolio_management.prompt_loader import load_prompt; "
            "from portfolio_management.contracts import AgentRole; "
            "bundles = [load_prompt(role, PromptSet.development()) for role in AgentRole]; "
            "assert len(bundles) == 4; "
            "assert all(b.status == 'development' for b in bundles); "
            "assert all(b.owner_approval_ref is None and b.module_hashes for b in bundles); "
            "print(portfolio_management.__file__)",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr
    assert str(environment) in probe.stdout
