"""Offline transport scripts for real Strands agents, never provider calls."""

import asyncio
import copy
import json

from strands.models import Model


class ScriptModel(Model):
    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []

    def update_config(self, **kwargs):
        pass

    def get_config(self):
        return {"model_id": "offline-fake"}

    async def structured_output(self, *args, **kwargs):
        raise AssertionError("legacy structured_output bypasses generation hooks")
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.calls.append(
            copy.deepcopy(
                {
                    "messages": messages,
                    "tools": tool_specs,
                    "tool_choice": kwargs.get("tool_choice"),
                }
            )
        )
        if not self.steps:
            raise AssertionError("offline model script exhausted")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, asyncio.Event):
            await step.wait()
            step = {"text": "released"}
        yield {"messageStart": {"role": "assistant"}}
        if "name" in step:
            yield {
                "contentBlockStart": {
                    "start": {
                        "toolUse": {
                            "toolUseId": f"call-{len(self.calls)}",
                            "name": step["name"],
                        }
                    }
                }
            }
            yield {
                "contentBlockDelta": {
                    "delta": {
                        "toolUse": {
                            "input": json.dumps(step.get("input", {})),
                        }
                    }
                }
            }
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": step["text"]}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use" if "name" in step else "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 2, "outputTokens": 3, "totalTokens": 5},
                "metrics": {"latencyMs": 1},
            }
        }


def output(name, **values):
    return {"name": name, "input": values}
