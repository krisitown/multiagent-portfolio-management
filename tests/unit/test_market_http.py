"""Exercise the real bridge adapter with an in-memory HTTP transport only."""

import asyncio
import importlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from wheel_team.contracts.evidence import MarketQuery

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "market-provider"


def api():
    try:
        return importlib.import_module("wheel_team.adapters.market.http")
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("wheel_team.adapters.market"):
            pytest.fail("missing Task10 implementation: http")
        raise


def query(**updates):
    return MarketQuery(
        query_id=updates.pop("query_id", "quote-delayed"),
        capability=updates.pop("capability", "underlying_quotes"),
        instrument_ids=updates.pop("instrument_ids", ("SYN-A",)),
        limit=2,
        **updates,
    )


def payload(query_id="quote-delayed"):
    stored = json.loads((EXAMPLES / "responses.json").read_text())["responses"]["quote-delayed"]
    return {**stored, "query_id": query_id}


def provider(handler, **updates):
    return api().HttpMarketProvider(
        endpoint=updates.pop("endpoint", "https://synthetic-bridge.invalid/v1"),
        capabilities=updates.pop("capabilities", ("underlying_quotes",)),
        transport=httpx.MockTransport(handler),
        max_retries=updates.pop("max_retries", 0),
        retry_delay_seconds=0,
        **updates,
    )


async def test_operator_bridge_receives_only_fixed_readonly_path_and_typed_query():
    requests = []

    async def serve(request):
        requests.append(request)
        return httpx.Response(200, json=payload())

    selected = provider(serve)
    result = await selected.query(query())
    assert result.status == "success"
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "https://synthetic-bridge.invalid/v1/query"
    assert json.loads(requests[0].content) == query().model_dump(mode="json")
    assert requests[0].extensions["timeout"]["read"] > 0


@pytest.mark.parametrize(
    "endpoint",
    [
        "ftp://synthetic-bridge.invalid/v1",
        "https://user:secret@synthetic-bridge.invalid/v1",
        "https://synthetic-bridge.invalid/v1?api_key=secret",
        "https://synthetic-bridge.invalid/v1#secret",
        "/relative-bridge",
    ],
)
def test_unsafe_operator_endpoint_is_rejected_before_transport(endpoint):
    requests = []

    def serve(request):
        requests.append(request)
        return httpx.Response(200, json=payload())

    with pytest.raises(ValueError):
        provider(serve, endpoint=endpoint)
    assert requests == []


@pytest.mark.parametrize(
    "parameters",
    [
        {"endpoint": "https://other.invalid/stolen"},
        {"base_url": "https://other.invalid/stolen"},
        {"url": "https://other.invalid/stolen"},
        {"api_key": "synthetic-model-supplied-secret"},
        {"headers": {"Authorization": "synthetic-model-supplied-secret"}},
        {"credentials": {"token": "synthetic-model-supplied-secret"}},
    ],
)
async def test_model_parameters_cannot_supply_transport_configuration(parameters):
    requests = []

    async def serve(request):
        requests.append(request)
        return httpx.Response(200, json=payload())

    result = await provider(serve).query(query(parameters=parameters))
    assert result.status == "invalid_data"
    assert result.evidence == ()
    assert requests == []
    assert "synthetic-model-supplied-secret" not in result.model_dump_json()


async def test_unsupported_capability_is_local_unavailable_without_http():
    requests = []

    async def serve(request):
        requests.append(request)
        return httpx.Response(200, json=payload())

    result = await provider(serve).query(query(capability="intraday_ticks"))
    assert result.status == "unavailable"
    assert result.complete is False
    assert result.evidence == ()
    assert result.reason
    assert requests == []


@pytest.mark.parametrize("failure", [429, 503, "timeout"])
async def test_transient_failure_stops_after_two_retries_and_is_never_empty_success(failure):
    requests = []

    async def serve(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return httpx.Response(failure, text="synthetic private error body")

    result = await provider(serve, max_retries=2).query(query())
    assert len(requests) == 3
    assert result.status == "retryable_error"
    assert result.evidence == ()
    assert result.complete is False
    assert result.reason
    assert "synthetic private error body" not in result.model_dump_json()


async def test_retry_keeps_original_source_time_delay_and_provenance():
    requests = []

    async def serve(request):
        requests.append(request)
        return httpx.Response(429) if len(requests) == 1 else httpx.Response(200, json=payload())

    result = await provider(serve, max_retries=2).query(query())
    assert result.status == "success"
    assert len(requests) == 2
    evidence = result.evidence[0]
    assert evidence.source_timestamp == datetime(2026, 10, 29, 15, tzinfo=UTC)
    assert evidence.source_time_raw == "2026-10-29T15:00:00Z"
    assert evidence.delay_state == "delayed"
    assert evidence.delay_seconds == 900
    assert evidence.provenance == ("synthetic-quotes", "synthetic-dataset-v1")
    assert evidence.fetched_at >= datetime(2026, 10, 29, 15, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, "permanent_error"),
        (403, "permanent_error"),
        (404, "unavailable"),
        (400, "permanent_error"),
    ],
)
async def test_permanent_http_failure_is_not_retried(status, expected):
    requests = []

    async def serve(request):
        requests.append(request)
        return httpx.Response(status, text="synthetic-private-provider-error")

    result = await provider(serve, max_retries=2).query(query())
    assert result.status == expected
    assert result.complete is False
    assert result.evidence == ()
    assert len(requests) == 1
    assert "synthetic-private-provider-error" not in result.model_dump_json()


@pytest.mark.parametrize("malformation", ["json", "version", "naive_fetch", "mismatch", "missing"])
async def test_invalid_wire_data_is_reported_without_retry_or_partial_success(malformation):
    requests = []

    async def serve(request):
        requests.append(request)
        data = payload()
        if malformation == "json":
            return httpx.Response(200, text="{broken")
        if malformation == "version":
            data["schema_version"] = 2
        elif malformation == "naive_fetch":
            data["evidence"][0]["fetched_at"] = "2026-10-29T15:05:00"
        elif malformation == "mismatch":
            data["query_id"] = "other-consumer"
        elif malformation == "missing":
            del data["status"]
        return httpx.Response(200, json=data)

    result = await provider(serve, max_retries=2).query(query())
    assert result.status == "invalid_data"
    assert result.evidence == ()
    assert result.complete is False
    assert len(requests) == 1


async def test_redirect_never_contacts_a_different_origin():
    requests = []

    async def serve(request):
        requests.append(str(request.url))
        return httpx.Response(307, headers={"location": "https://other.invalid/stolen"})

    result = await provider(serve, max_retries=2).query(query())
    assert result.status == "permanent_error"
    assert requests == ["https://synthetic-bridge.invalid/v1/query"]


async def test_environment_credential_is_private_and_source_urls_are_never_fetched(monkeypatch):
    monkeypatch.setenv("WHEEL_SYNTHETIC_BRIDGE_KEY", "synthetic-env-secret")
    requests = []

    async def serve(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-env-secret"
        return httpx.Response(200, json=payload())

    result = await provider(serve, api_key_env="WHEEL_SYNTHETIC_BRIDGE_KEY").query(query())
    assert result.status == "success"
    assert len(requests) == 1
    assert result.evidence[0].source_url == "https://synthetic-quotes.invalid/synthetic"
    assert "synthetic-env-secret" not in result.model_dump_json()
    assert "WHEEL_SYNTHETIC_BRIDGE_KEY" not in result.model_dump_json()


async def test_provider_exception_does_not_disclose_credentials_or_endpoint(monkeypatch):
    monkeypatch.setenv("WHEEL_SYNTHETIC_BRIDGE_KEY", "synthetic-env-secret")

    async def serve(request):
        raise httpx.ConnectError(
            "synthetic-env-secret WHEEL_SYNTHETIC_BRIDGE_KEY https://synthetic-bridge.invalid/v1",
            request=request,
        )

    result = await provider(serve, api_key_env="WHEEL_SYNTHETIC_BRIDGE_KEY").query(query())
    assert result.status == "retryable_error"
    assert "synthetic-env-secret" not in result.model_dump_json()
    assert "WHEEL_SYNTHETIC_BRIDGE_KEY" not in result.model_dump_json()
    assert "synthetic-bridge.invalid" not in result.model_dump_json()


async def test_identical_inflight_fetch_is_shared_but_consumer_ids_and_data_are_detached():
    entered = asyncio.Event()
    release = asyncio.Event()
    requests = []

    async def serve(request):
        requests.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(200, json=payload(json.loads(request.content)["query_id"]))

    selected = provider(serve)
    first = asyncio.create_task(selected.query(query(query_id="consumer-a")))
    await asyncio.wait_for(entered.wait(), timeout=1)
    second = asyncio.create_task(selected.query(query(query_id="consumer-b")))
    await asyncio.sleep(0)
    release.set()
    left, right = await asyncio.wait_for(asyncio.gather(first, second), timeout=1)
    assert len(requests) == 1
    assert left.query_id == "consumer-a"
    assert right.query_id == "consumer-b"
    assert left.evidence[0].content["bid"] == right.evidence[0].content["bid"] == "20.10"
    left.evidence[0].content["bid"] = "0"
    assert right.evidence[0].content["bid"] == "20.10"
    assert left.evidence[0].source_timestamp == right.evidence[0].source_timestamp


@pytest.mark.parametrize(
    "difference", ["instrument", "as_of", "cursor", "freshness", "entitlement"]
)
async def test_distinct_requested_data_never_share_an_inflight_fetch(difference):
    release = asyncio.Event()
    two_entered = asyncio.Event()
    requests = []

    async def serve(request):
        requests.append(request)
        if len(requests) == 2:
            two_entered.set()
        await release.wait()
        return httpx.Response(200, json=payload(json.loads(request.content)["query_id"]))

    changes = {
        "instrument": {"instrument_ids": ("SYN-B",)},
        "as_of": {"as_of": datetime(2026, 10, 28, tzinfo=UTC)},
        "cursor": {"cursor": "another-page"},
        "freshness": {"parameters": {"freshness_seconds": 60}},
        "entitlement": {"parameters": {"entitlement": "synthetic-delayed"}},
    }
    selected = provider(serve)
    tasks = [
        asyncio.create_task(selected.query(query(query_id="left"))),
        asyncio.create_task(selected.query(query(query_id="right", **changes[difference]))),
    ]
    try:
        await asyncio.wait_for(two_entered.wait(), timeout=1)
    finally:
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)
    assert len(requests) == 2
    assert [result.query_id for result in results] == ["left", "right"]


async def test_completed_fetch_does_not_become_an_implicit_stale_cache():
    requests = []

    async def serve(request):
        requests.append(request)
        return httpx.Response(200, json=payload())

    selected = provider(serve)
    await selected.query(query())
    await selected.query(query())
    assert len(requests) == 2


async def test_cancelling_one_consumer_does_not_cancel_the_shared_fetch():
    entered = asyncio.Event()
    release = asyncio.Event()
    requests = []

    async def serve(request):
        requests.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(200, json=payload(json.loads(request.content)["query_id"]))

    selected = provider(serve)
    first = asyncio.create_task(selected.query(query(query_id="cancelled-consumer")))
    await asyncio.wait_for(entered.wait(), timeout=1)
    second = asyncio.create_task(selected.query(query(query_id="surviving-consumer")))
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    result = await asyncio.wait_for(second, timeout=1)
    assert result.status == "success"
    assert result.query_id == "surviving-consumer"
    assert len(requests) == 1


async def test_enforced_timeout_cancels_blocked_transport_and_returns_retryable_error():
    calls = []

    async def serve(request):
        calls.append(request)
        await asyncio.Future()

    result = await asyncio.wait_for(provider(serve, timeout_seconds=0.01).query(query()), timeout=1)
    assert result.status == "retryable_error"
    assert result.complete is False
    assert len(calls) == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"timeout_seconds": 0},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": float("nan")},
        {"max_retries": -1},
        {"max_retries": 1.5},
        {"max_retries": True},
    ],
)
def test_invalid_retry_and_timeout_limits_are_rejected_before_transport(updates):
    with pytest.raises(ValueError):
        provider(lambda request: httpx.Response(200, json=payload()), **updates)


async def test_missing_configured_credential_never_sends_unauthenticated_request(monkeypatch):
    monkeypatch.delenv("WHEEL_SYNTHETIC_BRIDGE_KEY", raising=False)
    requests = []

    async def serve(request):
        requests.append(request)
        return httpx.Response(200, json=payload())

    with pytest.raises(ValueError) as caught:
        provider(serve, api_key_env="WHEEL_SYNTHETIC_BRIDGE_KEY")
    assert requests == []
    assert "WHEEL_SYNTHETIC_BRIDGE_KEY" not in str(caught.value)


async def test_nested_transport_configuration_is_rejected_without_echo():
    requests = []

    async def serve(request):
        requests.append(request)
        return httpx.Response(200, json=payload())

    result = await provider(serve).query(
        query(
            parameters={
                "filters": [{"API_KEY": "synthetic-model-supplied-secret"}],
            }
        )
    )
    assert result.status == "invalid_data"
    assert requests == []
    assert "synthetic-model-supplied-secret" not in result.model_dump_json()


@pytest.mark.parametrize("token", ["synthetic-env-secret", 'synthetic-quoted-"secret\\value'])
async def test_success_payload_cannot_echo_configured_secret_into_evidence(monkeypatch, token):
    monkeypatch.setenv("WHEEL_SYNTHETIC_BRIDGE_KEY", token)

    async def serve(request):
        data = payload()
        data["evidence"][0]["content"]["leaked"] = token
        return httpx.Response(200, json=data)

    result = await provider(serve, api_key_env="WHEEL_SYNTHETIC_BRIDGE_KEY").query(query())
    assert result.status == "invalid_data"
    assert result.evidence == ()
    assert token not in result.model_dump_json()


async def test_explicit_bridge_retryable_result_is_retried_with_same_bound():
    requests = []

    async def serve(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "query_id": "quote-delayed",
                "status": "retryable_error",
                "reason": "Synthetic bridge source is rate limited.",
            },
        )

    result = await provider(serve, max_retries=2).query(query())
    assert result.status == "retryable_error"
    assert result.complete is False
    assert len(requests) == 3


async def test_last_consumer_cancellation_releases_transport_and_inflight_slot():
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    requests = []

    async def serve(request):
        requests.append(request)
        if len(requests) > 1:
            return httpx.Response(200, json=payload())
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    selected = provider(serve)
    running = asyncio.create_task(selected.query(query()))
    await asyncio.wait_for(entered.wait(), timeout=1)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    result = await asyncio.wait_for(selected.query(query()), timeout=1)
    assert result.status == "success"
    assert len(requests) == 2


@pytest.mark.parametrize("token", ["synthetic-secret-é", "synthetic-secret-🔒"])
async def test_nonascii_credential_fails_safely_before_httpx_client_construction(
    monkeypatch, token
):
    monkeypatch.setenv("WHEEL_SYNTHETIC_BRIDGE_KEY", token)
    requests = []

    async def serve(request):
        requests.append(request)
        return httpx.Response(200, json=payload())

    constructed = False
    with pytest.raises(ValueError) as caught:
        selected = provider(serve, api_key_env="WHEEL_SYNTHETIC_BRIDGE_KEY")
        constructed = True
        # Exercise real HTTPX client/header construction if preflight accepts the token.
        await selected.query(query())
    assert type(caught.value) is ValueError
    assert constructed is False
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert token not in repr(caught.value)
    assert "WHEEL_SYNTHETIC_BRIDGE_KEY" not in repr(caught.value)
    assert requests == []


@pytest.mark.parametrize(
    "number", ["NaN", "Infinity", "-Infinity", "1e309", "-1e309", "1e999", "-1e999"]
)
async def test_nonfinite_response_json_is_invalid_without_retry_or_null_coercion(number):
    requests = []
    data = payload()
    data["evidence"][0]["content"]["metric"] = "numeric-probe"
    wire = json.dumps(data).replace('"numeric-probe"', number)

    async def serve(request):
        requests.append(request)
        return httpx.Response(200, content=wire)

    result = await provider(serve, max_retries=2).query(query())
    assert result.status == "invalid_data"
    assert result.complete is False
    assert result.evidence == ()
    assert len(requests) == 1


@pytest.mark.parametrize("number, expected", [("null", None), ("0", 0), ("0.0", 0.0)])
async def test_response_json_preserves_real_null_and_numeric_zero_distinctly(number, expected):
    data = payload()
    data["evidence"][0]["content"]["metric"] = "numeric-probe"
    wire = json.dumps(data).replace('"numeric-probe"', number)

    async def serve(request):
        return httpx.Response(200, content=wire)

    result = await provider(serve).query(query())
    assert result.status == "success"
    actual = result.evidence[0].content["metric"]
    assert actual == expected
    assert type(actual) is type(expected)
    round_trip = json.loads(result.model_dump_json())["evidence"][0]["content"]["metric"]
    assert round_trip == expected
    assert type(round_trip) is type(expected)
