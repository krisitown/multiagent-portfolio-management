"""Small, read-only Prometheus sampler. Never attributes server rates to an agent."""

import math
import re
import threading
import time
import urllib.request
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any
    ) -> None:
        return None


class MetricsSource:
    def __init__(self, url: str | None):
        if url:
            parsed = urlsplit(url)
            if (
                parsed.scheme != "http"
                or parsed.hostname not in ("127.0.0.1", "localhost")
                or parsed.path != "/metrics"
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Metrics URL must be http://127.0.0.1:PORT/metrics")
        self.url = url
        self._prior: tuple[float, dict[str, float | None]] | None = None
        self._lock = threading.Lock()
        self._sampled = 0.0
        self._cached: dict[str, Any] = {
            "available": False,
            "scope": "server-wide",
            "reason": "not configured",
            "sample_period_seconds": 5,
        }

    def observe(self, text: str, at: float) -> dict[str, Any]:
        samples: dict[str, list[tuple[str, float]]] = {}
        for line in text.splitlines():
            match = re.fullmatch(r"(vllm:[\w:]+)(\{[^}]*\})?\s+([^\s]+)(?:\s+\d+)?", line)
            if match:
                try:
                    value = float(match[3])
                except ValueError:
                    continue
                if math.isfinite(value):
                    samples.setdefault(match[1], []).append((match[2] or "", value))

        def total(name: str, label: str | None = None) -> float | None:
            values = [
                v
                for labels, v in samples.get("vllm:" + name, [])
                if label is None or label in labels
            ]
            return sum(values) if values else None

        counters = {
            "generation": total("generation_tokens_total"),
            "local_compute_prompt": total(
                "prompt_tokens_by_source_total", 'source="local_compute"'
            ),
            "cache_hit": total("prompt_tokens_by_source_total", 'source="local_cache_hit"'),
        }
        rates = {}
        for name, counter_value in counters.items():
            rate = None
            if self._prior and counter_value is not None:
                before = self._prior[1].get(name)
                elapsed = at - self._prior[0]
                if before is not None and counter_value >= before and elapsed > 0:
                    rate = (counter_value - before) / elapsed
            rates[name + "_tok_s"] = rate
        kv = total("kv_cache_usage_perc")
        if kv is None:
            kv = total("gpu_cache_usage_perc")
        result = {
            "available": True,
            "scope": "server-wide",
            "sample_period_seconds": 5,
            "sampled_at": datetime.now(UTC).isoformat(),
            "rate_interval_seconds": at - self._prior[0] if self._prior else None,
            "running": total("num_requests_running"),
            "waiting": total("num_requests_waiting"),
            "kv_percent": kv * 100 if kv is not None else None,
            "preemptions_total": total("num_preemptions_total"),
            **rates,
            "limitations": "All requests on this server; "
            "source-separated prompt counters required. "
            "Rates are sampled counter deltas, not request decode or prefill speed.",
        }
        self._prior = at, counters
        return result

    def poll(self) -> dict[str, Any]:
        if not self.url:
            return self._cached
        with self._lock:
            now = time.monotonic()
            if now - self._sampled < 5:
                return self._cached
            self._sampled = now
            try:
                opener = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))
                with opener.open(self.url, timeout=0.8) as response:
                    data = response.read(2 * 1024 * 1024 + 1)
                if len(data) > 2 * 1024 * 1024:
                    raise ValueError("Metrics body limit")
                self._cached = self.observe(data.decode("utf-8"), now)
            except Exception:
                self._prior = None
                self._cached = {
                    "available": False,
                    "scope": "server-wide",
                    "reason": "endpoint unavailable",
                    "sample_period_seconds": 5,
                }
            return self._cached
