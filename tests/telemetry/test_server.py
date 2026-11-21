import http.client
import json
import threading
from contextlib import contextmanager

import pytest

from wheel_team.telemetry.server import make_server


@contextmanager
def running(tmp_path, **kwargs):
    server = make_server(tmp_path, port=0, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def get(server, path="/", headers=None, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    conn.request(method, path, headers=headers or {})
    res = conn.getresponse()
    body = res.read()
    hdr = dict(res.getheaders())
    conn.close()
    return res.status, hdr, body


def test_only_loopback_with_host_origin_and_no_cors(tmp_path):
    with running(tmp_path) as server:
        assert server.server_address[0] == "127.0.0.1"
        assert get(server)[0] == 200
        assert get(server, headers={"Host": "evil.example"})[0] == 403
        assert get(server, headers={"Origin": "https://evil.example"})[0] == 403
        assert (
            get(
                server,
                headers={
                    "Host": f"localhost:{server.server_port}",
                    "Origin": f"http://localhost:{server.server_port}",
                },
            )[0]
            == 200
        )
        status, headers, _ = get(server, "/api/run")
        assert status == 200
        assert "Access-Control-Allow-Origin" not in headers
        assert "default-src 'self'" in headers["Content-Security-Policy"]
        assert "no-store" in headers["Cache-Control"]
        assert get(server, "/", method="POST")[0] == 405
    with pytest.raises(ValueError):
        make_server(tmp_path, port=0, host="0.0.0.0")


@pytest.mark.parametrize(
    "path",
    [
        "/../.env",
        "/%2e%2e/.env",
        "/report.md",
        "/trace.jsonl",
        "/.env",
        "/api/file?path=/etc/passwd",
        "//evil.example/",
        "/static/../../../etc/passwd",
    ],
)
def test_no_arbitrary_files(tmp_path, path):
    (tmp_path / ".env").write_text("PRIVATE")
    with running(tmp_path) as server:
        status, _, body = get(server, path)
        assert status in (400, 404)
        assert b"PRIVATE" not in body


def test_summary_is_small_and_private_payloads_are_lazy(tmp_path):
    malicious = "<img src=x onerror=alert(1)>"
    events = [
        {
            "event": "model_generation_started",
            "invocation_id": "lead",
            "role": "team_lead",
            "generation": 1,
        },
        {
            "event": "tool_call_finished",
            "invocation_id": "lead",
            "name": "fetch_sources",
            "call_id": "call",
            "status": "success",
            "result": [{"text": malicious}],
        },
    ]
    (tmp_path / "trace.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (tmp_path / "report.md").write_text(malicious)
    with running(tmp_path) as server:
        _, _, body = get(server, "/api/run")
        data = json.loads(body)
        assert "result" not in data["invocations"][0]["tools"][0]
        _, _, body = get(server, "/api/invocation?id=lead")
        assert json.loads(body)["output"] == malicious
        _, headers, body = get(server, "/api/tool?invocation=lead&call=call")
        assert headers["Content-Type"].startswith("application/json")
        assert json.loads(body)["result"][0]["text"] == malicious
        assert b"onerror" not in get(server, "/")[2]
        rev = data["revision"]
        assert json.loads(get(server, "/api/run?after=" + str(rev))[2])["unchanged"]
        assert get(server, "/api/events?after=invalid")[0] == 400
        metrics = json.loads(get(server, "/api/metrics")[2])
        assert metrics["available"] is False
        assert metrics["scope"] == "server-wide"


def test_metrics_reject_remote_endpoints(tmp_path):
    for url in [
        "https://example.com/metrics",
        "http://169.254.169.254/",
        "file:///etc/passwd",
        "http://localhost:8000/other",
    ]:
        with pytest.raises(ValueError):
            make_server(tmp_path, port=0, metrics_url=url)


def test_restart_cannot_reuse_a_different_snapshot_revision(tmp_path):
    (tmp_path / "report.md").write_text("AAPL old report")
    with running(tmp_path) as first:
        revision = json.loads(get(first, "/api/run")[2])["revision"]
    (tmp_path / "report.md").write_text("AAPL new report")
    with running(tmp_path) as second:
        data = json.loads(get(second, "/api/run?after=" + str(revision))[2])
        assert not data.get("unchanged")
        assert data["report"] is not None


def test_failure_projection_exposes_runtime_origin_and_unknown_overlap(tmp_path):
    rows = [
        {
            "event": "model_generation_started",
            "invocation_id": "analyst:a:x",
            "role": "analyst",
            "generation": 1,
            "at": "2026-10-31T14:00:00+00:00",
        },
        {
            "event": "model_generation_started",
            "invocation_id": "lead",
            "role": "team_lead",
            "generation": 2,
            "at": "2026-10-31T14:00:01+00:00",
        },
        {
            "event": "model_generation_finished",
            "invocation_id": "lead",
            "role": "team_lead",
            "generation": 2,
            "status": "failed",
            "at": "2026-10-31T14:00:02+00:00",
        },
        {
            "event": "run_completed",
            "completion_reason": "failed",
            "at": "2026-10-31T14:00:03+00:00",
        },
    ]
    (tmp_path / "trace.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    report = b"# Team response\n\nRun stopped before final synthesis.\n"
    (tmp_path / "report.md").write_bytes(report)
    with running(tmp_path) as server:
        summary = json.loads(get(server, "/api/run")[2])
        assert summary["completion_reason"] == "failed"
        assert summary["totals"]["peak_concurrency"] is None
        assert summary["totals"]["unclosed_model_starts"] == 1
        lead = json.loads(get(server, "/api/invocation?id=lead")[2])
        assert lead["status"] == "failed"
        assert lead["output_label"] == "Runtime fallback report"
        assert lead["output"].encode() == report
    assert (tmp_path / "report.md").read_bytes() == report
