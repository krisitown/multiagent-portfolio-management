"""Prompt readiness and run-snapshot behavior; development prose is not expertise."""

import hashlib
import importlib
import json

import pytest

from portfolio_management.contracts import AgentRole


def api(module):
    try:
        return importlib.import_module(f"portfolio_management.{module}")
    except ModuleNotFoundError:
        pytest.fail(f"missing Task3 implementation: {module}")


def write_bundle(root, *, status="approved", approval="owner-synthetic-approval"):
    root.mkdir(exist_ok=True)
    content = "# Synthetic approved test fixture\n\nReturn an advisory structured result.\n"
    role = root / "analyst.md"
    role.write_text(content)
    shared = root / "common.md"
    shared.write_text("# Synthetic common fixture\n\nNever place broker orders.\n")
    module = root / "module.md"
    module.write_text("# Synthetic illustrative knowledge fixture\n\nNo market claims.\n")

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    manifest = root / "manifest.json"
    payload = {
        "prompt_set_id": "synthetic-approved",
        "version": "1",
        "status": status,
        "owner_approval_ref": approval,
        "roles": {"analyst": {"path": "analyst.md", "content_hash": digest(role)}},
        "shared": {"common": {"path": "common.md", "content_hash": digest(shared)}},
        "modules": {"synthetic": {"path": "module.md", "content_hash": digest(module)}},
    }
    manifest.write_text(json.dumps(payload))
    return manifest, role, shared, module, payload


def test_production_requires_owner_approval(tmp_path):
    manifest, *_ = write_bundle(tmp_path, approval=None)
    config, loader = api("config"), api("prompt_loader")
    selected = config.PromptSet(manifest_path=manifest, mode="production")
    with pytest.raises(loader.PromptReadinessError, match="approval"):
        loader.load_prompt(AgentRole.ANALYST, selected)


@pytest.mark.parametrize("changed", ["role", "shared", "module"])
def test_approval_hash_covers_each_effective_markdown_file(tmp_path, changed):
    manifest, role, shared, module, _ = write_bundle(tmp_path)
    {"role": role, "shared": shared, "module": module}[changed].write_text("Changed Markdown\n")
    config, loader = api("config"), api("prompt_loader")
    with pytest.raises(loader.PromptReadinessError, match="hash"):
        loader.load_prompt(
            AgentRole.ANALYST, config.PromptSet(manifest_path=manifest, mode="production")
        )


def test_development_requires_explicit_selection(tmp_path):
    manifest, *_ = write_bundle(tmp_path, status="development", approval=None)
    config, loader = api("config"), api("prompt_loader")
    with pytest.raises(loader.PromptReadinessError, match="development"):
        loader.load_prompt(
            AgentRole.ANALYST, config.PromptSet(manifest_path=manifest, mode="production")
        )
    bundle = loader.load_prompt(
        AgentRole.ANALYST, config.PromptSet(manifest_path=manifest, mode="development")
    )
    assert bundle.status == "development"
    assert bundle.owner_approval_ref is None


def test_loaded_bundle_is_frozen_and_new_load_uses_reapproved_markdown(tmp_path):
    manifest, role, shared, module, payload = write_bundle(tmp_path)
    config, loader = api("config"), api("prompt_loader")
    selected = config.PromptSet(manifest_path=manifest, mode="production")
    first = loader.load_prompt(AgentRole.ANALYST, selected)
    assert "advisory structured result" in first.content
    assert first.content_hash == hashlib.sha256(role.read_bytes()).hexdigest()
    assert first.shared_hashes == {"common": hashlib.sha256(shared.read_bytes()).hexdigest()}
    assert first.module_hashes == {"synthetic": hashlib.sha256(module.read_bytes()).hexdigest()}
    role.write_text("# Revised synthetic fixture\n\nReturn an explicitly limited result.\n")
    payload["roles"]["analyst"]["content_hash"] = hashlib.sha256(role.read_bytes()).hexdigest()
    payload["owner_approval_ref"] = "owner-synthetic-reapproval"
    manifest.write_text(json.dumps(payload))
    second = loader.load_prompt(AgentRole.ANALYST, selected)
    assert "advisory structured result" in first.content
    assert "explicitly limited result" not in first.content
    assert "explicitly limited result" in second.content
    assert first.content_hash != second.content_hash
    assert second.owner_approval_ref == "owner-synthetic-reapproval"


def test_development_resources_load_from_unrelated_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config, loader = api("config"), api("prompt_loader")
    for role in AgentRole:
        bundle = loader.load_prompt(role, config.PromptSet.development())
        assert bundle.role == role
        assert bundle.status == "development"
        assert bundle.owner_approval_ref is None
        assert bundle.content_hash
        assert bundle.module_hashes


def test_missing_production_manifest_reports_readiness_error(tmp_path):
    config, loader = api("config"), api("prompt_loader")
    with pytest.raises(loader.PromptReadinessError, match="manifest"):
        loader.load_prompt(
            AgentRole.ANALYST,
            config.PromptSet(manifest_path=tmp_path / "absent.json", mode="production"),
        )
