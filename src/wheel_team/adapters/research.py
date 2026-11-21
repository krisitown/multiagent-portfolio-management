"""Bounded process access to the existing Hermes read-only Tavily tools."""

import asyncio
import json
import math
import re
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from wheel_team.contracts.evidence import ResultState

MAX_RESPONSE_BYTES = 256 * 1024
Transport = Callable[[tuple[str, ...], bytes, str | None, int], Awaitable[bytes]]

# Constant code only. Operator paths and every model-supplied value arrive on stdin.
# stdout is exclusively the bounded JSON result; backend logs/errors are never forwarded.
BRIDGE_CODE = """
import asyncio, contextlib, hashlib, io, json, os, signal, sys
p = json.loads(sys.stdin.buffer.read(32769))
if hasattr(signal, 'setitimer'):
    signal.setitimer(signal.ITIMER_REAL, p['timeout_seconds'] + 1)
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from dotenv import load_dotenv
        load_dotenv(os.path.expanduser(p['env_file']), override=False)
        from tools.web_tools import web_search_tool, web_extract_tool
        if p['operation'] == 'search':
            result = json.loads(web_search_tool(p['query'], limit=p['limit']))
            if result.get('success') is not True:
                result = {'success': False}
            else:
                rows = result['data']['web']
                result = {'success': True, 'data': {'web': rows[:p['limit']]},
                          'results_truncated': len(rows) > p['limit']}
        elif p['operation'] == 'fetch':
            result = json.loads(asyncio.run(web_extract_tool(
                p['urls'], format='markdown', use_llm_processing=False)))
            rows = result['results']
            result = {'results': rows[:len(p['urls'])],
                      'results_truncated': len(rows) > len(p['urls'])}
        else:
            raise ValueError('unsupported operation')
        rows = result.get('data', {}).get('web', result.get('results', []))
        texts = []
        for row in rows:
            text = (row.get('description') if p['operation'] == 'search' else
                    row.get('raw_content') or row.get('content')) or ''
            if not isinstance(text, str):
                raise ValueError('invalid source text')
            texts.append(text)
        budgets = [0] * len(rows)
        remaining = p['max_chars']
        if p['operation'] == 'fetch':
            active = [i for i, text in enumerate(texts)
                      if text.strip() and not rows[i].get('error')]
            # Max-min shares: short sources release unused room to other valid sources.
            while active and remaining:
                share = max(1, remaining // len(active))
                for i in active:
                    take = min(share, len(texts[i]) - budgets[i], remaining)
                    budgets[i] += take
                    remaining -= take
                active = [i for i in active if budgets[i] < len(texts[i])]
        else:
            # Preserve the existing ranked search-snippet allocation.
            for i, text in enumerate(texts):
                budgets[i] = min(len(text), remaining)
                remaining -= budgets[i]
        normalized = []
        for i, row in enumerate(rows):
            text = texts[i]
            kept = text[:budgets[i]]
            normalized.append({'url': row.get('url'), 'title': str(row.get('title') or '')[:500],
                'content': kept,
                'original_chars': max(len(text), row.get('original_chars', len(text))),
                'content_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'truncated': bool(row.get('truncated')) or len(kept) < len(text),
                'content_hash_scope': 'available_extracted_text_before_bridge_budget',
                'error': bool(row.get('error')),
                'source_time_raw': row.get('source_timestamp') or row.get('published_date')
                                   or row.get('publication_date')})
            if p['operation'] == 'fetch':
                normalized[-1].update({'available_chars': len(text),
                    'source_empty': not text.strip(),
                    'upstream_truncated': bool(row.get('truncated')),
                    'budget_truncated': len(kept) < len(text) and not row.get('error')})
        if p['operation'] == 'search' and result.get('success'):
            result['data']['web'] = normalized
        elif p['operation'] == 'fetch':
            result['results'] = normalized
except Exception:
    result = {'backend_error': True}
sys.stdout.write(json.dumps(result, ensure_ascii=False))
"""


class ResearchBackendConfig(BaseModel):
    """Private deployment selection; no backend is configured implicitly."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: Literal["local", "ssh"] = "local"
    python: str = Field(min_length=1, max_length=4096)
    cwd: str = Field(min_length=1, max_length=4096)
    env_file: str = Field(min_length=1, max_length=4096)
    ssh_host: str | None = None
    ssh_identity_file: str | None = None
    timeout_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    max_chars: int = Field(default=12000, gt=0, le=12000, strict=True)

    @field_validator("python", "cwd", "env_file", "ssh_identity_file")
    @classmethod
    def no_control_characters(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or any(ord(c) < 32 for c in value)):
            raise ValueError("Backend paths must be nonempty without control characters")
        return value

    @model_validator(mode="after")
    def selected_transport(self) -> Self:
        if self.mode == "ssh":
            if not self.ssh_host or not re.fullmatch(
                r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", self.ssh_host
            ):
                raise ValueError("SSH requires a configured hostname/user reference")
        elif self.ssh_host is not None or self.ssh_identity_file is not None:
            raise ValueError("Local backend does not accept SSH settings")
        return self


class ResearchError(RuntimeError):
    def __init__(self, status: ResultState, reason: str):
        self.status, self.reason = status, reason
        super().__init__(reason)


async def subprocess_transport(
    argv: tuple[str, ...], stdin: bytes, cwd: str | None, max_bytes: int
) -> bytes:
    """No shell, bounded stdout, discarded stderr; cancellation kills and awaits child."""
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(stdin)
        await process.stdin.drain()
        process.stdin.close()
        chunks, size = [], 0
        while block := await process.stdout.read(min(65536, max_bytes + 1 - size)):
            chunks.append(block)
            size += len(block)
            if size > max_bytes:
                raise ResearchError("invalid_data", "Research response exceeds the byte bound")
        code = await process.wait()
        if code != 0:
            raise ResearchError("permanent_error", "Research backend process failed")
        return b"".join(chunks)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise


class HermesResearchBackend:
    def __init__(self, config: ResearchBackendConfig, *, transport: Transport | None = None):
        self.config = config
        self._transport = transport or subprocess_transport

    def describe(self) -> dict[str, Any]:
        return {
            "mode": "connected",
            "provider": "hermes_public_web",
            "source": "Tavily",
            "capabilities": ["public_search", "fetch_sources"],
            "search_limit": {"minimum": 1, "maximum": 5},
            "fetch_url_limit": {"minimum": 1, "maximum": 5},
            "total_text_chars": self.config.max_chars,
            "document_verification": (
                "not provided; extraction may contain navigation or partial text"
            ),
        }

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        selected = self.config
        body = json.dumps(
            {
                **payload,
                "env_file": selected.env_file,
                "max_chars": selected.max_chars,
                "timeout_seconds": selected.timeout_seconds,
            },
            ensure_ascii=False,
        ).encode()
        if len(body) > 32768:
            raise ResearchError("invalid_data", "Research request exceeds the byte bound")
        argv: tuple[str, ...]
        if selected.mode == "local":
            argv = (str(Path(selected.python).expanduser()), "-c", BRIDGE_CODE)
            cwd: str | None = str(Path(selected.cwd).expanduser())
        else:
            remote = (
                "cd -- "
                + shlex.quote(selected.cwd)
                + " && exec "
                + shlex.join([selected.python, "-c", BRIDGE_CODE])
            )
            args = [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=" + str(math.ceil(min(selected.timeout_seconds, 10))),
            ]
            if selected.ssh_identity_file:
                args.extend(["-i", str(Path(selected.ssh_identity_file).expanduser())])
            assert selected.ssh_host is not None
            argv = (*args, "--", selected.ssh_host, remote)
            cwd = None
        try:
            async with asyncio.timeout(selected.timeout_seconds):
                raw = await self._transport(argv, body, cwd, MAX_RESPONSE_BYTES)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ResearchError("invalid_data", "Research response exceeds the byte bound")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
            if result.get("backend_error"):
                raise ResearchError("permanent_error", "Research backend failed")
            return result
        except TimeoutError:
            raise ResearchError("retryable_error", "Research backend timed out") from None
        except ResearchError:
            raise
        except (ValueError, UnicodeError):
            raise ResearchError("invalid_data", "Research backend returned invalid JSON") from None
        except Exception:
            raise ResearchError("permanent_error", "Research backend failed") from None
