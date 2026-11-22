"""Generic file mechanics; no financial interpretation."""

import os
from pathlib import Path

import pytest

from portfolio_management.continuity import ContinuityError, ContinuityFiles


def test_empty_create_paginated_read_update_history(tmp_path):
    files = ContinuityFiles(tmp_path, writable=True)
    assert files.continuity_list() == {"files": [], "next_offset": None}
    first = files.continuity_write("vault/cases/a.md", "α\r\nsecond\n", None)
    read = files.continuity_read("vault/cases/a.md", max_lines=1)
    assert read["content"] == "α\r\n"
    assert read["next_start_line"] == 2
    assert read["revision"] == first["revision"]
    assert files.continuity_read(read["snapshot_path"], start_line=2)["content"] == "second\n"
    second = files.continuity_write("vault/cases/a.md", "Changed\n", read["revision"])
    assert files.continuity_read(read["snapshot_path"])["content"] == "α\r\nsecond\n"
    assert second["revision"] != first["revision"]
    assert (
        files.continuity_write("vault/cases/a.md", "Changed\n", second["revision"])["changed"]
        is False
    )
    assert files.continuity_search("CHANGED")["matches"][0]["line"] == 1
    assert ContinuityFiles(tmp_path).continuity_read("vault/cases/a.md")["content"] == "Changed\n"


def test_conflict_lost_response_and_partial_work(tmp_path):
    f = ContinuityFiles(tmp_path, writable=True)
    a = f.continuity_write("vault/a.md", "unresolved", None)
    with pytest.raises(ContinuityError, match="ALREADY_EXISTS"):
        f.continuity_write("vault/a.md", "unresolved", None)
    assert f.continuity_read("vault/a.md")["revision"] == a["revision"]
    f.continuity_write("vault/a.md", "contrary evidence", a["revision"])
    with pytest.raises(ContinuityError, match="REVISION_CONFLICT"):
        f.continuity_write("vault/a.md", "stale", a["revision"])
    assert len(f.writes) == 2
    assert len(ContinuityFiles(tmp_path).continuity_list()["files"]) == 1


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/a.md",
        "vault/../sources/a.md",
        "vault/link/a.md",
        "sources/a.md",
        "history/a.md",
        "vault/a.txt",
        "vault/.lock",
    ],
)
def test_bad_write_paths_and_symlinks(tmp_path, path):
    f = ContinuityFiles(tmp_path, writable=True)
    (tmp_path / "vault/link").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ContinuityError):
        f.continuity_write(path, "x", None)


def test_readonly_source_and_limits(tmp_path):
    f = ContinuityFiles(tmp_path)
    with pytest.raises(ContinuityError, match="ACCESS_DENIED"):
        f.continuity_write("vault/a.md", "x", None)
    ref = f.save_source("run-1", "assessment", " exact α\r\n")
    assert ref.startswith("sources/run-1/")
    assert f.continuity_read(ref)["content"] == " exact α\r\n"
    with pytest.raises(ContinuityError, match="NOT_FOUND"):
        f.continuity_read("vault/missing.md")
    w = ContinuityFiles(tmp_path, writable=True)
    with pytest.raises(ContinuityError, match="TOO_LARGE"):
        w.continuity_write("vault/a.md", "a" * 262145, None)
    w.continuity_write("vault/a.md", "x" * 65537, None)
    with pytest.raises(ContinuityError, match="TOO_LARGE"):
        w.continuity_read("vault/a.md")


def test_interrupted_replace_preserves_current_and_recoverable_proposal(tmp_path, monkeypatch):
    f = ContinuityFiles(tmp_path, writable=True)
    first = f.continuity_write("vault/a.md", "old", None)
    real = os.replace

    def fail(src, dst):
        if Path(dst) == tmp_path / "vault/a.md":
            raise OSError("interrupted")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(ContinuityError, match="IO_ERROR"):
        f.continuity_write("vault/a.md", "proposed", first["revision"])
    assert f.continuity_read("vault/a.md")["content"] == "old"
    assert len(f.continuity_list("history/")["files"]) == 2
    assert len(f.writes) == 1


def test_exposed_schemas_defaults_errors_and_hidden_temps(tmp_path):
    from portfolio_management.continuity import continuity_tools

    f = ContinuityFiles(tmp_path, writable=True)
    audit = []
    tools = continuity_tools(f, audit, lambda: None)
    assert set(tools) == {
        "continuity_list",
        "continuity_read",
        "continuity_search",
        "continuity_write",
    }
    for tool in tools.values():
        assert tool.tool_spec["inputSchema"]["json"]["additionalProperties"] is False
    assert tools["continuity_list"].tool_spec["inputSchema"]["json"]["properties"]["limit"] == {
        "type": "integer",
        "default": 100,
        "minimum": 1,
        "maximum": 500,
    }
    assert tools["continuity_read"].tool_spec["inputSchema"]["json"]["required"] == ["path"]
    assert tools["continuity_write"].tool_spec["inputSchema"]["json"]["required"] == [
        "path",
        "content",
        "expected_revision",
    ]
    (tmp_path / "vault/.continuity-temp").write_text("pending")
    assert f.continuity_list()["files"] == []
    for start in (True, False, 0, 2147483648):
        with pytest.raises(ContinuityError, match="INVALID_ARGUMENT"):
            f.continuity_read("vault/no.md", start_line=start)


def test_restart_scenarios_preserve_meaning_and_exact_versions(tmp_path):
    """Synthetic ordinary records; plumbing does not choose financial conclusions."""
    texts = [
        "Review 1: unresolved funding question; no owner feedback.",
        "Review 2: contrary source evidence; prior question remains unresolved.",
        "Owner correction: preference differs. Prior agent view was not owner adoption.",
        "Review 3: unchanged observations. No feedback and no execution established.",
    ]
    snapshots = []
    expected = None
    for text in texts:
        files = ContinuityFiles(tmp_path, writable=True)
        if expected:
            current = files.continuity_read("vault/topic.md")
            assert current["revision"] == expected
        result = files.continuity_write("vault/topic.md", text, expected)
        expected = result["revision"]
        snapshots.append(result["snapshot_path"])
    restarted = ContinuityFiles(tmp_path)
    assert [restarted.continuity_read(p)["content"] for p in snapshots] == texts
    assert restarted.continuity_search("no execution")["matches"][0]["path"] == "vault/topic.md"


def test_distinct_process_writers_serialize_compare_and_replace(tmp_path):
    from concurrent.futures import ProcessPoolExecutor

    f = ContinuityFiles(tmp_path, writable=True)
    rev = f.continuity_write("vault/shared.md", "original", None)["revision"]
    with ProcessPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(_race_write, [(str(tmp_path), rev, "first"), (str(tmp_path), rev, "second")])
        )
    assert sorted(results) == ["REVISION_CONFLICT", "saved"]


def _race_write(args):
    root, rev, text = args
    try:
        ContinuityFiles(Path(root), writable=True).continuity_write("vault/shared.md", text, rev)
        return "saved"
    except ContinuityError as exc:
        return exc.body["error"]["code"]


def test_new_ancestor_directories_are_fsynced_before_success(tmp_path, monkeypatch):
    import stat

    real = os.fsync
    calls = []

    def observe(fd):
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            calls.append(info.st_ino)
        return real(fd)

    monkeypatch.setattr(os, "fsync", observe)
    root = tmp_path / "new-root"
    f = ContinuityFiles(root, writable=True)
    f.continuity_write("vault/new/deep/a.md", "durable", None)
    # Each new directory entry requires flushing its own containing directory.
    for parent in [
        tmp_path,
        root,
        root / "vault",
        root / "vault/new",
        root / "history",
        root / "history/new",
        root / "history/new/deep",
        root / "history/new/deep/a.md",
        root / "vault/new/deep",
    ]:
        assert parent.stat().st_ino in calls, str(parent)
    assert calls.index((root / "vault").stat().st_ino) < calls.index(
        (root / "vault/new").stat().st_ino
    )


def test_ancestor_flush_failure_returns_no_success_receipt(tmp_path, monkeypatch):
    import stat

    f = ContinuityFiles(tmp_path, writable=True)
    target_parent = (tmp_path / "vault").stat().st_ino
    real = os.fsync

    def fail(fd):
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode) and info.st_ino == target_parent:
            raise OSError("directory flush failed")
        return real(fd)

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(ContinuityError, match="IO_ERROR"):
        f.continuity_write("vault/new/a.md", "content", None)
    assert not f.writes
