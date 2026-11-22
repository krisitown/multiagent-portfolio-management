"""Bounded public-source research over operator-configured SSH; no service or model.

All host, identity and remote paths are private operator inputs. Agent requests are
validated JSON data on stdin; they cannot select commands, paths or environment.
"""

import asyncio
import hashlib
import ipaddress
import json
import re
import shlex
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from portfolio_management.contracts import EvidenceRecord, Finding, MarketQuery, MarketResult

from .base import failure, query_identity

CAPABILITIES = (
    "fundamentals",
    "financial_statements",
    "events",
    "underlying_quotes",
    "underlying_history",
    "option_chains",
    "relevant_news",
)
BOOTSTRAP = (
    "import json,sys; p=json.load(sys.stdin); "
    "exec(compile(p['source'], '<public-research-helper>', 'exec'), "
    "{'__name__':'__main__','REQUEST':p['request'],'CONFIG':p['config']})"
)


@dataclass(frozen=True)
class SSHResearchConfig:
    market_python: str
    research_python: str
    scanner_path: str
    hermes_project: str
    hermes_env: str
    capture_root: Path
    transport: Literal["ssh", "local"] = "ssh"
    host: str | None = None
    user: str | None = None
    identity_file: Path | None = None
    host_key_alias: str | None = None
    timeout_seconds: float = 90
    helper_path: Path | None = None

    def __post_init__(self) -> None:
        if self.transport not in {"ssh", "local"}:
            raise ValueError("Select local or SSH research transport")
        identities = (self.host, self.user, self.host_key_alias)
        if self.transport == "local" and any(
            value is not None for value in (*identities, self.identity_file)
        ):
            raise ValueError("Local market research does not accept SSH identity settings")
        if self.transport == "ssh" and self.identity_file is None:
            raise ValueError("SSH market research requires an explicit identity file")
        for value in identities if self.transport == "ssh" else ():
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:@-]+", value):
                raise ValueError("SSH identities require trusted operator configuration")
            if value.startswith("-"):
                raise ValueError("SSH identities cannot be options")
        for value in (
            self.market_python,
            self.research_python,
            self.scanner_path,
            self.hermes_project,
            self.hermes_env,
        ):
            if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
                raise ValueError("Remote paths must be absolute operator configuration")
        if not 1 <= self.timeout_seconds <= 180:
            raise ValueError("Research timeout must be between 1 and 180 seconds")


def public_url(value: str, *, resolve: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 2000 or any(c.isspace() for c in value):
        raise ValueError("Invalid public source URL")
    parsed = urlsplit(value)
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or "." not in host
        or host.endswith((".local", ".internal", ".localhost"))
    ):
        raise ValueError("Source URL must address public HTTPS content")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = []
    if resolve and not addresses:
        addresses = [
            ipaddress.ip_address(item[4][0])
            for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ]
    if any(not address.is_global for address in addresses):
        raise ValueError("Private source targets are forbidden")
    return value


def latest_history(frame: dict[str, Any]) -> dict[str, Any]:
    """Keep aligned recent daily bars; preserve source strings and infer no timezone."""
    table = frame["table"]
    index = frame["index_raw"]
    count = len(index)
    if len(table["index"]) != count or len(table["data"]) != count:
        raise ValueError("History indices and rows must align")
    dates = [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in index]
    if len({value.tzinfo is not None for value in dates}) > 1:
        raise ValueError("History cannot mix resolved and unresolved timezones")
    chronological = sorted(range(count), key=dates.__getitem__)
    selected = chronological[-80:]
    return frame | {
        "index_raw": [index[i] for i in selected],
        "table": table
        | {
            "index": [table["index"][i] for i in selected],
            "data": [table["data"][i] for i in selected],
        },
        "coverage": {
            "original_rows": count,
            "returned_rows": len(selected),
            "omitted_rows": count - len(selected),
            "ordering": "chronological_ascending",
            "selection": "most_recent_rows",
            "original_first_index_raw": index[chronological[0]] if count else None,
            "original_last_index_raw": index[chronological[-1]] if count else None,
            "returned_first_index_raw": index[selected[0]] if selected else None,
            "returned_last_index_raw": index[selected[-1]] if selected else None,
            "full_raw_retained": True,
        },
    }


class SparkMarketProvider:
    """Each query collects one bounded observation and records original helper JSON."""

    def __init__(self, config: SSHResearchConfig) -> None:
        self.config = config
        helper = config.helper_path or (Path(__file__).with_name("collector.py"))
        self._source = helper.read_text(encoding="utf-8")
        self._helper_sha256 = hashlib.sha256(self._source.encode()).hexdigest()
        self._observed_symbols: set[str] = set()

    def capabilities(self) -> list[str]:
        return list(CAPABILITIES)

    def describe(self) -> dict[str, Any]:
        return {
            "mode": "connected",
            "provider": "shared_public_market_collector",
            "capabilities": self.capabilities(),
            "coverage": {
                "market_wide": False,
                "scope": "One explicitly requested ticker per bounded source query",
                "observed_company_ids": sorted(self._observed_symbols),
            },
            "sources": ["Yahoo Finance", "CBOE delayed options with Yahoo fallback"],
            "source_helper_sha256": self._helper_sha256,
            "source_commit": "06f67ab",
            "query_parameters": "One uppercase instrument_id; no as_of/cursor; "
            "parameters only for option_chains (see option_chains_parameters)",
            "option_chains_parameters": {
                "discovery": {},
                "browse_example": {
                    "selection": {
                        "expiry": "2027-01-15",
                        "option_right": "call",
                        "strike_min": "1",
                        "strike_max": "500",
                        "offset": 0,
                        "limit": 40,
                    },
                },
                "browse_bounds": "one expiry/right; optional strike_min/strike_max decimal "
                "strings using exact strike syntax; offset 0..10000, limit 1..40 default20; "
                "ascending strike pages with total/next_offset; no wheel filters; "
                "selection and contracts mutually exclusive; source can change between pages",
                "exact_example": {
                    "contracts": [
                        {"expiry": "2026-11-06", "option_right": "put", "strike": "110"},
                        {"expiry": "2027-01-15", "option_right": "call", "strike": "95.5"},
                    ],
                    "include_expiries": True,
                },
                "expiry_metadata_only": {"include_expiries": True},
                "bounds": "1–16 unique contracts, at most 4 expiries; expiry YYYY-MM-DD; "
                "option_right put/call; strike positive decimal string <=99999.999, "
                "1–5 integer digits, up to 3 fractional digits, no leading zeros/sign/exponent; "
                "include_expiries if supplied must be true; metadata max80 with omissions",
                "limits": "Exact selections bypass discovery DTE/OTM/eight-strike filters; "
                "source supplied standard symbol series only, no adjusted deliverable guarantee; "
                "each missing/unavailable contract and unknown quote field is explicit; "
                "CBOE delayed primary, Yahoo fallback on source failure; "
                "trade time/snapshot/fetch time are not contract bid/ask quote time; "
                "coverage always partial, executability unverified",
            },
        }

    def _request(self, query: MarketQuery) -> dict[str, Any]:
        query_identity(query)  # Reject non-finite JSON values.
        if query.cursor is not None or query.as_of is not None:
            raise ValueError("Live research does not provide historical-as-of or cursor semantics")
        params = dict(query.parameters)
        if query.capability in {"web_search", "source_documents"}:
            if len(query.instrument_ids) > 1:
                raise ValueError("At most one optional company identity is supported")
            allowed = (
                {"query", "limit"} if query.capability == "web_search" else {"urls", "max_chars"}
            )
            if set(params) - allowed:
                raise ValueError("Unrecorded research parameters")
            if query.capability == "web_search":
                text, count = params.get("query"), params.get("limit", 3)
                if (
                    not isinstance(text, str)
                    or not 1 <= len(text) <= 500
                    or type(count) is not int
                    or not 1 <= count <= 5
                ):
                    raise ValueError("Search bounds invalid")
            else:
                urls, chars = params.get("urls"), params.get("max_chars", 12000)
                if (
                    not isinstance(urls, list)
                    or not 1 <= len(urls) <= 3
                    or type(chars) is not int
                    or not 1000 <= chars <= 12000
                ):
                    raise ValueError("Source extraction bounds invalid")
                for url in urls:
                    if not isinstance(url, str):
                        raise ValueError("URL must be text")
                    public_url(url)
        elif query.capability == "option_chains" and params:
            if len(query.instrument_ids) != 1 or set(params) - {
                "contracts",
                "include_expiries",
                "selection",
            }:
                raise ValueError("Exact option selection requires one symbol and bounded fields")
            if "include_expiries" in params and params["include_expiries"] is not True:
                raise ValueError("include_expiries must be true when supplied")
            if "selection" in params:
                selection = params["selection"]
                if (
                    "contracts" in params
                    or not isinstance(selection, dict)
                    or not {"expiry", "option_right"} <= set(selection)
                    or set(selection)
                    - {"expiry", "option_right", "strike_min", "strike_max", "offset", "limit"}
                ):
                    raise ValueError("Browse requires bounded expiry/right selection")
                expiry = selection["expiry"]
                if not isinstance(expiry, str) or not re.fullmatch(
                    r"[0-9]{4}-[0-9]{2}-[0-9]{2}", expiry
                ):
                    raise ValueError("Expiry must be YYYY-MM-DD")
                date.fromisoformat(expiry)
                if selection["option_right"] not in {"call", "put"}:
                    raise ValueError("option_right must be call or put")
                for field in ("strike_min", "strike_max"):
                    if field in selection:
                        value = selection[field]
                        if (
                            not isinstance(value, str)
                            or not re.fullmatch(r"(?:0|[1-9][0-9]{0,4})(?:\.[0-9]{1,3})?", value)
                            or Decimal(value) <= 0
                        ):
                            raise ValueError("Strike bounds must be positive decimal strings")
                if Decimal(str(selection.get("strike_min", "0"))) > Decimal(
                    str(selection.get("strike_max", "99999.999"))
                ):
                    raise ValueError("Strike bounds are reversed")
                for field, default, lower, upper in (("limit", 20, 1, 40), ("offset", 0, 0, 10000)):
                    value = selection.get(field, default)
                    if type(value) is not int or not lower <= value <= upper:
                        raise ValueError("Browse pagination exceeds supported bounds")
            if "contracts" in params:
                contracts = params["contracts"]
                if not isinstance(contracts, list) or not 1 <= len(contracts) <= 16:
                    raise ValueError("Select 1–16 exact contracts")
                identities = set()
                expiries = set()
                for contract in contracts:
                    if not isinstance(contract, dict) or set(contract) != {
                        "expiry",
                        "option_right",
                        "strike",
                    }:
                        raise ValueError("Exact contract fields are required")
                    expiry, right, strike = (
                        contract["expiry"],
                        contract["option_right"],
                        contract["strike"],
                    )
                    if not isinstance(expiry, str) or not re.fullmatch(
                        r"[0-9]{4}-[0-9]{2}-[0-9]{2}", expiry
                    ):
                        raise ValueError("Expiry must be YYYY-MM-DD")
                    date.fromisoformat(expiry)
                    if not isinstance(right, str) or right not in {"put", "call"}:
                        raise ValueError("option_right must be put or call")
                    if (
                        not isinstance(strike, str)
                        or not re.fullmatch(r"(?:0|[1-9][0-9]{0,4})(?:\.[0-9]{1,3})?", strike)
                        or Decimal(strike) <= 0
                    ):
                        raise ValueError("Strike must be a bounded positive decimal string")
                    identities.add((expiry, right, Decimal(strike)))
                    expiries.add(expiry)
                if len(identities) != len(contracts) or len(expiries) > 4:
                    raise ValueError("Unique contracts and at most four expiries required")
        elif params or len(query.instrument_ids) != 1:
            raise ValueError("Company research requires one symbol and no extra parameters")
        for symbol in query.instrument_ids:
            if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,11}", symbol):
                raise ValueError("Invalid public ticker symbol")
        return {
            "capability": query.capability,
            "symbol": query.instrument_ids[0] if query.instrument_ids else None,
            "parameters": params,
        }

    async def _collect(self, request: dict[str, Any]) -> dict[str, Any]:
        if request["capability"] == "source_documents":
            for url in request["parameters"]["urls"]:
                await asyncio.to_thread(public_url, url, resolve=True)
        research = request["capability"] in {"web_search", "source_documents"}
        interpreter = self.config.research_python if research else self.config.market_python
        envelope = {
            "source": self._source,
            "request": request,
            "config": {
                "scanner_path": self.config.scanner_path,
                "hermes_project": self.config.hermes_project,
                "hermes_env": self.config.hermes_env,
            },
        }
        argv: tuple[str, ...]
        if self.config.transport == "local":
            argv = (interpreter, "-c", BOOTSTRAP)
        else:
            assert self.config.identity_file is not None
            argv = (
                "ssh",
                "-T",
                "-i",
                str(Path(self.config.identity_file).expanduser()),
                "-o",
                f"HostKeyAlias={self.config.host_key_alias}",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=10",
                f"{self.config.user}@{self.config.host}",
                shlex.join((interpreter, "-c", BOOTSTRAP)),
            )
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(json.dumps(envelope, allow_nan=False).encode()),
                timeout=self.config.timeout_seconds,
            )
        except BaseException:
            process.kill()
            await process.wait()
            raise
        if process.returncode or len(stdout) > 8_000_000:
            raise ValueError("Research transport failed or exceeded its raw bound")
        decoded = json.loads(stdout)
        if not isinstance(decoded, dict):
            raise ValueError("Research output must be an object")
        return decoded

    async def query(self, query: MarketQuery) -> MarketResult:
        if query.capability not in CAPABILITIES:
            return failure(query, "unavailable", "Connected research capability is unavailable.")
        try:
            request = self._request(query)
        except (ValueError, TypeError):
            return failure(query, "invalid_data", "Research request violates supported bounds.")
        try:
            observation = await asyncio.wait_for(
                self._collect(request), timeout=self.config.timeout_seconds
            )
        except Exception:
            return failure(query, "retryable_error", "Connected research failed or timed out.")
        if observation.get("status") == "unavailable" or observation.get("data") is None:
            return failure(query, "unavailable", "Public source returned no usable observation.")
        try:
            result = self._result(query, request, observation)
            if request["symbol"] is not None and observation["synthetic"] is False:
                self._observed_symbols.add(request["symbol"])
            return result
        except Exception:
            return failure(
                query, "invalid_data", "Connected research returned invalid source data."
            )

    def _result(
        self, query: MarketQuery, request: dict[str, Any], observation: dict[str, Any]
    ) -> MarketResult:
        if (
            observation.get("schema_version") != 1
            or observation.get("capability") != query.capability
            or type(observation.get("synthetic")) is not bool
        ):
            raise ValueError("Research source identity mismatch")
        raw = json.dumps(
            observation, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        raw_hash = hashlib.sha256(raw).hexdigest()
        capture_id = "live-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
        folder = Path(self.config.capture_root) / capture_id
        folder.mkdir(parents=True, exist_ok=False)
        with (folder / "raw.json").open("xb") as raw_file:
            raw_file.write(raw)
        (folder / "raw.json").chmod(0o444)
        fetched = datetime.fromisoformat(observation["fetched_at"])
        if fetched.tzinfo is None:
            raise ValueError("Fetch time must be aware")
        units: dict[str, str] = {}
        truncated: list[dict[str, Any]] = []
        chars = request["parameters"].get("max_chars", 12000)
        budget = [26000]

        def bounded(value: Any, path: str = "data") -> Any:
            if isinstance(value, bool) or value is None:
                return value
            if isinstance(value, (int, float)):
                units["source_numeric_values"] = "source_reported_unit_unverified"
                field = path.rsplit(".", 1)[-1]
                if isinstance(source_data, dict):
                    currency = source_data.get("currency")
                    financial = source_data.get("financialCurrency")
                    if field in {
                        "totalDebt",
                        "totalCash",
                        "totalRevenue",
                        "freeCashflow",
                        "operatingCashflow",
                    } and isinstance(financial, str):
                        units[path] = financial
                    elif field in {
                        "regularMarketPrice",
                        "regularMarketDayHigh",
                        "regularMarketDayLow",
                        "regularMarketPreviousClose",
                        "bid",
                        "ask",
                    }:
                        units[path] = (
                            currency + "/share"
                            if isinstance(currency, str)
                            else "currency_unknown/share"
                        )
                    elif field in {
                        "profitMargins",
                        "operatingMargins",
                        "grossMargins",
                        "returnOnEquity",
                        "returnOnAssets",
                        "revenueGrowth",
                        "earningsGrowth",
                        "payoutRatio",
                        "trailingPE",
                        "forwardPE",
                    }:
                        units[path] = "ratio"
                return str(value)
            if isinstance(value, str):
                metadata = path.rsplit(".", 1)[-1] in {
                    "url",
                    "title",
                    "source_url",
                    "pubDate",
                    "published_time",
                    "publishDate",
                }
                limit = 2000 if metadata else min(chars, max(0, budget[0]))
                text = value[:limit]
                if not metadata:
                    budget[0] -= len(text)
                if len(text) < len(value):
                    truncated.append(
                        {"field": path, "original_chars": len(value), "returned_chars": len(text)}
                    )
                return text
            if isinstance(value, list):
                if len(value) > 80:
                    truncated.append(
                        {"field": path, "original_items": len(value), "returned_items": 80}
                    )
                return [bounded(v, f"{path}.{i}") for i, v in enumerate(value[:80])]
            if isinstance(value, dict):
                return {k: bounded(v, f"{path}.{k}") for k, v in value.items()}
            raise ValueError("Unsupported source data")

        source_data = observation["data"]
        if query.capability == "underlying_history":
            source_data = latest_history(source_data)
            coverage = source_data["coverage"]
            if coverage["omitted_rows"]:
                truncated.append(
                    {
                        "field": "underlying_history",
                        "original_items": coverage["original_rows"],
                        "returned_items": coverage["returned_rows"],
                        "selection": "most_recent_rows",
                        "ordering": "chronological_ascending",
                        "full_raw_retained": True,
                    }
                )
        if query.capability == "financial_statements":
            summarized: dict[str, Any] = {}
            for statement, frame in source_data.items():
                if not frame:
                    summarized[statement] = None
                    continue
                table = frame["table"]
                labels = frame["index_raw"]
                preferred = [
                    i
                    for i, label in enumerate(labels)
                    if any(
                        word in label.lower()
                        for word in (
                            "total revenue",
                            "net income",
                            "operating income",
                            "gross profit",
                            "total debt",
                            "total assets",
                            "total liabilities",
                            "stockholders equity",
                            "cash cash",
                            "cash and cash",
                            "free cash flow",
                            "operating cash flow",
                            "capital expenditure",
                            "cash dividends",
                            "cash flow from",
                        )
                    )
                ][:24]
                selected = preferred or list(range(min(24, len(labels))))
                summarized[statement] = {
                    "periods_raw": frame["columns_raw"][:3],
                    "rows": [
                        {"metric": labels[i], "values": table["data"][i][:3]} for i in selected
                    ],
                    "original_rows": len(labels),
                    "returned_rows": len(selected),
                    "original_periods": len(frame["columns_raw"]),
                    "returned_periods": min(3, len(frame["columns_raw"])),
                    "selection": (
                        "up to 24 matching balance/income/cash-flow metric labels; "
                        "first three returned fiscal periods"
                    ),
                    "financial_currency": "unknown; not inferred from trading currency",
                }
            source_data = summarized
            truncated.append(
                {
                    "field": "financial_statements",
                    "reason": "bounded metric/period selection; full raw tables retained",
                }
            )
        content = {
            "synthetic": observation.get("synthetic", False),
            "capture_id": capture_id,
            "instrument_id": request["symbol"],
            "capability": query.capability,
            "data": bounded(source_data),
            "limits": observation.get("limits", []),
            "truncation": truncated,
            "raw_source_sha256": raw_hash,
            "source_helper_sha256": self._helper_sha256,
            "retrieval_data_untrusted": True,
            "timestamp_provenance": observation.get("timestamp_provenance"),
        }
        stamp = observation.get("source_timestamp")
        # A source timestamp is accepted only if explicitly aware; naive raw time remains unknown.
        resolved = datetime.fromisoformat(stamp) if stamp else None
        if resolved is not None:
            resolved = resolved.astimezone(UTC) if resolved.tzinfo else None
        evidence = EvidenceRecord(
            evidence_id=capture_id + "-" + query.capability,
            source=observation["source"],
            fetched_at=fetched,
            source_timestamp=resolved,
            source_time_raw=observation.get("source_time_raw"),
            source_time_availability="known" if resolved else "unknown",
            timezone_state="resolved" if resolved else "unknown",
            source_timezone="UTC" if resolved else None,
            delay_state="unknown",
            content=content,
            units=units,
            provenance=(capture_id, raw_hash, self._helper_sha256),
            source_url=observation.get("source_url"),
            content_hash=raw_hash,
            coverage=(query.capability,),
        )
        # Bound the exact existing tool response before any persistence or success claim.
        result = MarketResult(
            query_id=query.query_id,
            status="success",
            evidence=(evidence,),
            complete=False,
            findings=(
                Finding(
                    code="coverage_partial",
                    severity="warning",
                    reason=(
                        "Connected public-source observation; coverage, source limits "
                        "and truncation remain explicit."
                    ),
                ),
            ),
        )
        if len(result.model_dump_json().encode()) > 60000:
            raise ValueError("Normalized source result exceeds the tool bound")
        files = {"evidence.json": evidence.model_dump_json().encode()}
        index = {
            "schema_version": 1,
            "capture_id": capture_id,
            "synthetic": observation.get("synthetic", False),
            "company_ids": [request["symbol"]] if request["symbol"] else [],
            "request": request,
            "source_helper_sha256": self._helper_sha256,
            "raw_sha256": raw_hash,
            "fetched_at": observation["fetched_at"],
            "entries": [
                {
                    "capability": query.capability,
                    "instrument_id": request["symbol"],
                    "complete": False,
                    "evidence_file": "evidence.json",
                    "sha256": hashlib.sha256(files["evidence.json"]).hexdigest(),
                }
            ],
        }
        files["index.json"] = json.dumps(index, sort_keys=True).encode()
        for name, payload in files.items():
            with (folder / name).open("xb") as target:
                target.write(payload)
            (folder / name).chmod(0o444)
        return result
