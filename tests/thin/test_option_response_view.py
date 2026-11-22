"""Offline representation checks; no account facts or trading acceptance cases."""

import copy
import json

import pytest

from tests.support.thin_sdk import ScriptModel, output
from tests.thin.test_agents import context
from portfolio_management.agents.base import build_role


def quote_rows():
    return [
        {
            "contract": f"mechanical-{i}",
            "bid": i / 10,
            "ask": i / 10 + 0.2,
            "quantity": -2 if i == 0 else 1,
            "multiplier": 100,
            "currency": "USD",
            "option_right": "put",
            "expiry": "2030-02-15",
            "deliverable": None,
            "source": "offline-mechanical-observation",
            "quote_time": None,
            "timezone_state": "unknown",
            "complete": False,
        }
        for i in range(12)
    ]


def restore(value):
    """Independent reference reader: catches field/type/order loss in the encoder."""
    if isinstance(value, list):
        return [restore(v) for v in value]
    if not isinstance(value, dict):
        return value
    if list(value) == ["$option_table"]:
        table = value["$option_table"]
        rows = []
        for row in table["rows"]:
            varying = dict(zip(table["columns"], row, strict=True))
            rows.append(
                {
                    k: restore(
                        table["shared_fields_for_every_row"][k]
                        if k in table["shared_fields_for_every_row"]
                        else varying[k]
                    )
                    for k in table["original_key_order"]
                }
            )
        return rows
    return {k: restore(v) for k, v in value.items()}


def original_text(value):
    return json.dumps(value, ensure_ascii=False)


def test_compact_values_and_order_reconstruct_without_mutating_original():
    from portfolio_management.option_response_view import compact_option_text

    payload = {"status": "partial", "rows": quote_rows(), "error": None}
    before = copy.deepcopy(payload)
    text = original_text(payload)
    packed_text = compact_option_text(text)
    packed = json.loads(packed_text)
    assert len(packed_text) < len(text)
    assert "$option_table" in packed_text
    assert "shared_fields_for_every_row" in packed["option_response_view"]
    assert original_text(restore(packed["data"])) == original_text(before)
    assert payload == before


@pytest.mark.parametrize("exception", ["missing", "order", "values", "types"])
def test_exceptional_rows_keep_missing_null_zero_false_and_identity(exception):
    from portfolio_management.option_response_view import compact_option_text

    rows = quote_rows()
    if exception == "missing":
        del rows[0]["deliverable"]
    elif exception == "order":
        rows[0]["bid"] = rows[0].pop("bid")
    elif exception == "values":
        rows[0].update(currency="EUR", multiplier=10, option_right="call", deliverable="special")
        rows[1].update(complete=None, bid=0, quantity=0)
    else:
        rows[0]["complete"] = 0
        rows[1]["complete"] = 0.0
        rows[2]["complete"] = -0.0
    text = original_text({"rows": rows, "nested": {"exact prose": "Do not shorten. " * 20}})
    packed = json.loads(compact_option_text(text))
    recovered = restore(packed["data"]) if "option_response_view" in packed else packed
    assert original_text(recovered) == text
    if exception in {"missing", "order"}:
        assert compact_option_text(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "not JSON",
        '{"x":1,"x":2}',
        '{"x":NaN}',
        '{"rows":[]}',
        '{"rows":[{"bid":0}]}',
        '{"$option_table":{"untrusted":"original"}}',
    ],
)
def test_unsupported_or_unprofitable_text_is_identical(text):
    from portfolio_management.option_response_view import compact_option_text

    assert compact_option_text(text) == text


async def test_real_sdk_model_input_compacts_only_options_and_retains_raw_history_trace(tmp_path):
    payload = {"status": "partial", "rows": quote_rows(), "findings": ["Unknown multiplier"]}
    before = copy.deepcopy(payload)

    async def market_query(query: dict | str) -> dict:
        """Return explicitly offline observations."""
        return payload

    model = ScriptModel(
        output("market_query", query={"capability": "option_chains"}),
        output("market_query", query={"capability": "fundamentals"}),
        output("market_query", query=json.dumps({"capability": "option_chains"})),
        {"text": "Representation checked offline"},
    )
    ctx = context(tmp_path, "analyst", model, {"market_query": market_query})
    ctx.config = ctx.config.model_copy(update={"child_output_mode": "direct_text"})
    agent = build_role("analyst", ctx)
    await agent.invoke_async("Inspect representations")
    results = [
        block["toolResult"]
        for message in model.calls[-1]["messages"]
        for block in message["content"]
        if "toolResult" in block
    ]
    texts = [result["content"][0]["text"] for result in results]
    assert "option_response_view" in texts[0]
    assert json.loads(texts[1]) == before
    assert "option_response_view" not in texts[1]
    assert "option_response_view" in texts[2]
    for text in (texts[0], texts[2]):
        assert original_text(restore(json.loads(text)["data"])) == original_text(before)
        assert len(text) < len(texts[1])
    assert payload == before
    history = json.dumps(agent.messages)
    assert "option_response_view" not in history
    trace = (tmp_path / "trace.jsonl").read_text()
    assert "option_response_view" not in trace
    finished = [
        json.loads(line)
        for line in trace.splitlines()
        if json.loads(line)["event"] == "tool_call_finished"
    ]
    assert len(finished) == 3
    assert all(json.loads(row["result"][0]["text"]) == before for row in finished)


async def test_real_sdk_explorer_options_liquidity_is_compacted(tmp_path):
    async def options_liquidity(symbols: list[str]) -> dict:
        """Offline mechanical option rows."""
        return {"rows": quote_rows()}

    model = ScriptModel(
        output("options_liquidity", symbols=["MECHANICAL"]), {"text": "Offline only"}
    )
    ctx = context(tmp_path, "explorer", model, {"options_liquidity": options_liquidity})
    ctx.config = ctx.config.model_copy(update={"child_output_mode": "direct_text"})
    await build_role("explorer", ctx).invoke_async("Inspect representation")
    result = model.calls[-1]["messages"][-1]["content"][0]["toolResult"]
    assert restore(json.loads(result["content"][0]["text"])["data"]) == {"rows": quote_rows()}


def test_message_projection_preserves_unrelated_and_error_blocks():
    from portfolio_management.option_response_view import option_response_messages

    raw = original_text({"rows": quote_rows()})
    names = ["finance_calculate", "public_search", "consult_expert", "options_liquidity"]
    messages = []
    for index, name in enumerate(names):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": [{"toolUse": {"toolUseId": str(index), "name": name, "input": {}}}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "toolResult": {
                                "toolUseId": str(index),
                                "status": "error" if name == "options_liquidity" else "success",
                                "content": [{"text": raw}, {"json": {"rows": quote_rows()}}],
                            }
                        }
                    ],
                },
            ]
        )
    before = copy.deepcopy(messages)
    assert option_response_messages(messages) == before
    assert messages == before


async def test_actual_market_tool_evidence_is_exact_while_sdk_input_is_compact(tmp_path):
    from tests.thin.test_vault import evidence
    from portfolio_management.contracts import EvidenceRecord, MarketResult
    from portfolio_management.tools.market import market_tools
    from portfolio_management.vault import EvidenceStore

    record = EvidenceRecord.model_validate(
        evidence(
            "mechanical-options", content={"capability": "option_chains", "rows": quote_rows()}
        )
    )
    before = record.model_dump(mode="json")

    class Provider:
        def capabilities(self):
            return ["option_chains"]

        async def query(self, query):
            return MarketResult(query_id=query.query_id, status="success", evidence=(record,))

    store = EvidenceStore(tmp_path / "records")
    tools = market_tools(store, Provider())
    model = ScriptModel(
        output("market_query", query={"query_id": "mechanical", "capability": "option_chains"}),
        {"text": "Offline evidence retained"},
    )
    ctx = context(tmp_path, "analyst", model, tools)
    ctx.config = ctx.config.model_copy(update={"child_output_mode": "direct_text"})
    agent = build_role("analyst", ctx)
    await agent.invoke_async("Inspect offline options")
    block = model.calls[-1]["messages"][-1]["content"][0]["toolResult"]
    decoded = restore(json.loads(block["content"][0]["text"])["data"])
    assert decoded["data"]["evidence"] == [before]
    assert decoded["evidence_refs"] == ["evidence:mechanical-options"]
    assert store.get("evidence:mechanical-options") == before
    assert record.model_dump(mode="json") == before
    assert "option_response_view" not in json.dumps(agent.messages)
