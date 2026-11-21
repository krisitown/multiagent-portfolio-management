"""Read-only loopback HTTP viewer; no runtime controls or arbitrary-file routes."""

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from .metrics import MetricsSource
from .reader import RunReader
from .state import RunState

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


def compact_lane(lane: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    result = {k: v for k, v in lane.items() if k not in ("output", "generations", "tools")}
    result["tools"] = [
        {k: v for k, v in t.items() if k not in ("inputs", "result")} for t in lane["tools"]
    ]
    result["generations"] = [
        {k: v for k, v in g.items() if detail or k != "text"} for g in lane["generations"]
    ]
    if detail:
        result["output"] = lane["output"]
    return result


class View:
    def __init__(self, run_dir: Path, metrics: MetricsSource):
        self.reader = RunReader(run_dir)
        self.state = RunState()
        self.cursor: str | None = None
        self.revision = 0
        self.epoch = uuid4().hex
        self.warnings: list[str] = []
        self.updated = 0.0
        self.lock = threading.RLock()
        self.metrics = metrics

    def refresh(self) -> None:
        if time.monotonic() - self.updated < 0.5:
            return
        # Incremental bounded passes; a large backlog is caught up on subsequent polls.
        result = self.reader.poll(self.cursor)
        self.cursor = result["cursor"]
        if result["reset"]:
            self.state = RunState()
        self.state.apply(result["events"])
        if result["events"] or result["reset"] or result["warnings"] != self.warnings:
            self.revision += 1
        self.warnings = result["warnings"]
        if result["more"]:
            self.warnings = [*self.warnings, "Catching up with recorded events"]
        self.updated = time.monotonic()

    def summary(self) -> dict[str, Any]:
        snapshot = self.state.snapshot()
        return {
            **snapshot,
            "revision": f"{self.epoch}:{self.revision}",
            "cursor": self.cursor,
            "warnings": self.warnings,
            "run_name": self.reader.run_dir.name,
            "invocations": [compact_lane(i) for i in snapshot["invocations"]],
            "events": [
                {k: v for k, v in e.items() if k not in ("text", "inputs", "result")}
                for e in snapshot["events"]
            ],
            "report": (
                {k: v for k, v in snapshot["report"].items() if k != "text"}
                if snapshot["report"]
                else None
            ),
        }


def make_server(
    run_dir: Path, *, port: int = 8765, host: str = "127.0.0.1", metrics_url: str | None = None
) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("Viewer binds to 127.0.0.1 only; use SSH forwarding")
    view = View(run_dir, MetricsSource(metrics_url))

    class Handler(BaseHTTPRequestHandler):
        server: ThreadingHTTPServer
        server_version = "WheelTelemetry"

        def log_message(self, format: str, *args: Any) -> None:
            pass  # Private URLs and query strings never enter access logs.

        def send_body(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def json(self, value: Any, code: int = 200) -> None:
            self.send_body(
                code,
                json.dumps(value, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )

        def valid_request(self) -> bool:
            hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            host_headers = self.headers.get_all("Host", [])
            origins = self.headers.get_all("Origin", [])
            if len(host_headers) != 1 or host_headers[0] not in hosts:
                return False
            if origins and (len(origins) != 1 or origins[0] != "http://" + host_headers[0]):
                return False
            return self.headers.get("Sec-Fetch-Site") not in ("cross-site",)

        def do_POST(self) -> None:
            self.json({"error": "read-only viewer"}, 405)

        do_PUT = do_POST
        do_DELETE = do_POST
        do_PATCH = do_POST
        do_OPTIONS = do_POST

        def do_GET(self) -> None:
            if not self.valid_request():
                self.json({"error": "Host or Origin rejected"}, 403)
                return
            if (
                len(self.path) > 14000
                or not self.path.startswith("/")
                or self.path.startswith("//")
            ):
                self.json({"error": "invalid path"}, 400)
                return
            url = urlsplit(self.path)
            if url.path in STATIC and not url.query:
                name, mime = STATIC[url.path]
                self.send_body(200, (Path(__file__).parent / "static" / name).read_bytes(), mime)
                return
            query = parse_qs(url.query)
            if any(len(v) != 1 for v in query.values()):
                self.json({"error": "invalid query"}, 400)
                return
            try:
                if url.path == "/api/metrics":
                    self.json(view.metrics.poll())
                    return
                with view.lock:
                    view.refresh()
                    if url.path == "/api/run":
                        if query.get("after", [""])[0] == f"{view.epoch}:{view.revision}":
                            self.json(
                                {"unchanged": True, "revision": f"{view.epoch}:{view.revision}"}
                            )
                        else:
                            self.json(view.summary())
                    elif url.path == "/api/events":
                        self.json(view.reader.poll(query.get("after", [None])[0]))
                    elif url.path == "/api/invocation":
                        lane = next(
                            (
                                i
                                for i in view.state.snapshot()["invocations"]
                                if i["id"] == query.get("id", [""])[0]
                            ),
                            None,
                        )
                        self.json(
                            compact_lane(lane, detail=True)
                            if lane
                            else {"error": "unknown invocation"},
                            200 if lane else 404,
                        )
                    elif url.path == "/api/tool":
                        lane = view.state.invocations.get(query.get("invocation", [""])[0])
                        tool = lane["tools"].get(query.get("call", [""])[0]) if lane else None
                        self.json(tool or {"error": "unknown call"}, 200 if tool else 404)
                    else:
                        self.json({"error": "not found"}, 404)
            except (ValueError, TypeError, KeyError, AttributeError):
                self.json({"error": "invalid request or malformed artifact"}, 400)
            except OSError:
                self.json({"error": "artifacts temporarily unavailable"}, 503)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def serve(run_dir: Path, port: int = 8765, metrics_url: str | None = None) -> int:
    server = make_server(run_dir, port=port, metrics_url=metrics_url)
    print(f"Private telemetry: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--metrics-url")
    args = parser.parse_args()
    return serve(args.run_dir, args.port, args.metrics_url)


if __name__ == "__main__":
    raise SystemExit(main())
