"""Direct-publisher parsing against public captures; no network calls in tests."""

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).parents[1] / "fixtures" / "explorer"


def publishers():
    try:
        return importlib.import_module("portfolio_management.adapters.explorer.publishers")
    except ImportError:
        pytest.fail("the standalone direct-publisher collector is missing")


@pytest.mark.parametrize(
    "series,fixture,want_date,want_value,want_units",
    [
        ("UNRATE", "publisher-BLS-UNRATE.json", "2026-09-01", 4.2, "percent"),
        ("CPIAUCSL", "publisher-BLS-CPIAUCSL.json", "2026-09-01", 336.298, "index_1982-1984=100"),
        ("PAYEMS", "publisher-BLS-PAYEMS.json", "2026-09-01", 159044, "thousands_of_persons"),
        ("DFF", "publisher-NYFed-DFF.json", "2026-10-29", 3.88, "percent"),
        ("DGS2", "publisher-Treasury-yields.xml", "2026-10-30", 4.56, "percent"),
        ("DGS10", "publisher-Treasury-yields.xml", "2026-10-30", 4.94, "percent"),
    ],
)
def test_direct_public_captures_keep_period_dates_and_units(
    monkeypatch, series, fixture, want_date, want_value, want_units
):
    module = publishers()
    monkeypatch.setattr(
        module, "_publisher_request", lambda *args, **kwargs: (FIXTURES / fixture).read_bytes()
    )
    result = module.fetch_publisher_series(series, "2026-01-01", "2026-10-31")
    assert result["status"] == "success"
    assert result["observations"][-1] == {"date": want_date, "value": want_value}
    assert result["metadata"]["units"] == want_units
    assert result["metadata"]["publisher"]
    assert result["source_url"].startswith("https://")
    assert "fred" not in result["source_url"].lower()
    assert result["fetched_at"].endswith("+00:00")
    assert all("2026-01-01" <= row["date"] <= "2026-10-31" for row in result["observations"])


def test_bls_missing_period_is_preserved_and_wrong_series_is_not_accepted(monkeypatch):
    module = publishers()
    raw = json.loads((FIXTURES / "publisher-BLS-UNRATE.json").read_bytes())
    monkeypatch.setattr(
        module, "_publisher_request", lambda *args, **kwargs: json.dumps(raw).encode()
    )
    result = module.fetch_publisher_series("UNRATE", "2025-01-01", "2026-10-31")
    assert {"date": "2025-10-01", "value": None} in result["observations"]
    raw["Results"]["series"][0]["seriesID"] = "WRONG"
    result = module.fetch_publisher_series("UNRATE", "2025-01-01", "2026-10-31")
    assert result["status"] == "unavailable"
    assert result["observations"] == []


def test_duplicate_or_nonfinite_source_observations_are_not_published(monkeypatch):
    module = publishers()
    raw = json.loads((FIXTURES / "publisher-NYFed-DFF.json").read_bytes())
    raw["refRates"].append(raw["refRates"][0])
    monkeypatch.setattr(
        module, "_publisher_request", lambda *args, **kwargs: json.dumps(raw).encode()
    )
    assert (
        module.fetch_publisher_series("DFF", "2026-01-01", "2026-10-31")["status"] == "unavailable"
    )
    raw["refRates"].pop()
    raw["refRates"][0]["percentRate"] = "NaN"
    assert (
        module.fetch_publisher_series("DFF", "2026-01-01", "2026-10-31")["status"] == "unavailable"
    )


def test_publisher_failure_is_sanitized_and_invalid_requests_never_fetch(monkeypatch):
    module = publishers()

    def fail(*args, **kwargs):
        raise RuntimeError("private-secret-untrusted-error")

    monkeypatch.setattr(module, "_publisher_request", fail)
    result = module.fetch_publisher_series("DFF", "2026-01-01", "2026-10-31")
    assert result["status"] == "unavailable"
    assert "private-secret" not in json.dumps(result)
    for series, start, end in [
        ("UNKNOWN", "2026-01-01", "2026-10-31"),
        ("DFF", "2026-10-31", "2026-01-01"),
        ("DFF", "bad", "2026-10-31"),
    ]:
        assert module.fetch_publisher_series(series, start, end)["status"] == "unavailable"


def test_http_response_is_stopped_at_byte_bound_and_closed(monkeypatch):
    module = publishers()

    class Response:
        closed = False

        def raise_for_status(self):
            pass

        @property
        def content(self):
            raise AssertionError("unbounded content must not be materialized")

        def iter_content(self, chunk_size):
            for _ in range(20):
                yield b"a" * 65536

        def close(self):
            self.closed = True

    response = Response()
    monkeypatch.setitem(
        sys.modules,
        "curl_cffi",
        SimpleNamespace(requests=SimpleNamespace(get=lambda *args, **kwargs: response)),
    )
    with pytest.raises(ValueError, match="bound"):
        module._publisher_request("https://api.bls.gov/")
    assert response.closed is True
