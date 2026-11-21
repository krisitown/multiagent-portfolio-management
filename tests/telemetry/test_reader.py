import json

import pytest

from wheel_team.telemetry.reader import RunReader


def row(event="run_started", **values):
    return json.dumps({"event": event, "at": "2026-10-31T14:00:00+00:00", **values}) + "\n"


def test_partial_reconnect_and_replaced_file(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text(row() + '{"event": "run_')
    reader = RunReader(tmp_path)
    first = reader.poll(None)
    assert [e["event"] for e in first["events"]] == ["run_started"]
    assert reader.poll(first["cursor"])["events"] == []
    with trace.open("a") as f:
        f.write('completed"}\n')
    second = reader.poll(first["cursor"])
    assert [e["event"] for e in second["events"]] == ["run_completed"]
    trace.unlink()
    trace.write_text(row("run_started", role="new"))
    replaced = reader.poll(second["cursor"])
    assert replaced["reset"] is True
    assert replaced["events"][0]["role"] == "new"


def test_same_inode_rewrite_and_malformed_tail(tmp_path):
    p = tmp_path / "trace.jsonl"
    p.write_text(row(role="old"))
    r = RunReader(tmp_path)
    a = r.poll(None)
    p.write_text(row(role="new") + "broken\n")
    b = r.poll(a["cursor"])
    assert b["reset"]
    assert b["warnings"]
    assert b["events"][0]["role"] == "new"


def test_artifacts_preserve_hash_and_only_allowlisted_data(tmp_path):
    import hashlib

    data = b"# AAPL\r\n<script>alert(1)</script>\n"
    (tmp_path / "report.md").write_bytes(data)
    (tmp_path / ".env").write_text("API_KEY=never")
    (tmp_path / "public-output.jsonl").write_text(
        row(
            "public_text",
            invocation_id="lead",
            generation=1,
            text="API_KEY=secret-value",
            reasoning="DO NOT RETAIN",
            authorization="hidden",
        )
    )
    result = RunReader(tmp_path).poll(None)
    report = next(e for e in result["events"] if e["event"] == "artifact_report")
    assert report["sha256"] == hashlib.sha256(data).hexdigest()
    assert report["text"] == data.decode()
    assert data == (tmp_path / "report.md").read_bytes()
    public = next(e for e in result["events"] if e["event"] == "public_text")
    assert "secret-value" not in json.dumps(public)
    assert "DO NOT RETAIN" not in json.dumps(public)
    assert "hidden" not in json.dumps(public)


def test_symlink_and_invalid_cursor_do_not_read_external_files(tmp_path):
    outside = tmp_path.parent / "external-private-trace"
    outside.write_text(row(role="secret"))
    (tmp_path / "trace.jsonl").symlink_to(outside)
    r = RunReader(tmp_path)
    assert not r.poll(None)["events"]
    with pytest.raises(ValueError):
        r.poll("../etc/passwd")


def test_campaign_discovery_is_explicit_and_unambiguous(tmp_path):
    native = tmp_path / "observation/native/run-one"
    native.mkdir(parents=True)
    (native / "trace.jsonl").write_text(row())
    assert RunReader(tmp_path).run_dir == native
    other = native.parent / "run-two"
    other.mkdir()
    (other / "trace.jsonl").write_text(row())
    with pytest.raises(ValueError):
        RunReader(tmp_path)


def test_middle_same_inode_same_size_replacement_is_explicit(tmp_path):
    trace = tmp_path / "trace.jsonl"
    text = row() * 100 + row("analyst_completed", job_id="a", outcome="failed") + row() * 100
    trace.write_text(text)
    reader = RunReader(tmp_path)
    first = reader.poll(None)
    trace.write_text(text.replace('"outcome": "failed"', '"outcome": "passed"'))
    second = reader.poll(first["cursor"])
    assert second["reset"]
    assert any(e.get("outcome") == "passed" for e in second["events"])
