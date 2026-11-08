"""TurnRunner's caps on model calls: per turn, and a budget across turns. A scripted model; no network, no AWS."""
import json

import pytest
from strands import tool
from strands.models import Model

from burn_agents.turn import CallBudget, TurnCut, TurnRunner, make_model


@tool
def look() -> str:
    """Look at something."""
    return "seen"


class Looping(Model):
    """Calls `look` `tools` times, then answers. With tools=None it never stops calling it."""

    def __init__(self, tools=None):
        self.calls, self.tools = 0, tools

    def update_config(self, **kw): ...
    def get_config(self): return {}

    async def structured_output(self, *a, **k):
        raise NotImplementedError
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kw):
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        if self.tools is None or self.calls <= self.tools:
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"t{self.calls}", "name": "look"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps({})}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockDelta": {"delta": {"text": "done"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}


def test_without_caps_a_turn_runs_to_the_end_and_returns_its_result():
    model = Looping(tools=2)
    result = TurnRunner(model, "be brief", [look])("go")
    assert model.calls == 3 and result.stop_reason == "end_turn"


def test_a_turn_is_cut_off_at_its_model_call_cap():
    model = Looping()
    with pytest.raises(TurnCut, match="stopped after 4 model calls in one turn"):
        TurnRunner(model, "be brief", [look], max_model_calls=4)("go")
    assert model.calls == 4


def test_a_budget_caps_model_calls_across_turns_and_counts_each_before_it_is_made():
    model, budget = Looping(), CallBudget(3)
    runner = TurnRunner(model, "be brief", [look], max_model_calls=10, budget=budget)
    with pytest.raises(TurnCut, match="model-call budget of 3 spent"):
        runner("first")
    assert model.calls == budget.used == 3 and budget.left == 0
    with pytest.raises(TurnCut):
        runner("second")
    assert model.calls == 3  # a spent budget makes no call at all


def test_make_model_takes_a_botocore_config_and_keeps_strands_own_without_one():
    import boto3
    from botocore.config import Config
    session = boto3.Session(aws_access_key_id="AKIAFAKEFAKEFAKEFAKE", aws_secret_access_key="x", region_name="us-east-1")
    plain = make_model("m", session)
    tuned = make_model("m", session, Config(retries={"mode": "adaptive", "total_max_attempts": 4}), max_tokens=512)
    assert tuned.client.meta.config.retries == {"mode": "adaptive", "total_max_attempts": 4}
    assert plain.client.meta.config.retries != tuned.client.meta.config.retries
    assert plain.config["temperature"] == tuned.config["temperature"] == 0
    assert tuned.config["max_tokens"] == 512 and "max_tokens" not in plain.config



def test_a_turn_past_its_deadline_makes_no_more_model_calls(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("burn_agents.turn.monotonic", lambda: now[0])

    @tool
    def slow() -> str:
        """Take a while."""
        now[0] += 40
        return "done slowly"

    class Slow(Looping):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kw):
            self.calls += 1
            yield {"messageStart": {"role": "assistant"}}
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"t{self.calls}", "name": "slow"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": "{}"}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}

    model = Slow()
    with pytest.raises(TurnCut, match="deadline of 100 s"):
        TurnRunner(model, "be brief", [slow], max_model_calls=10, turn_seconds=100)("go")
    assert model.calls == 3  # calls at 0, 40 and 80 s; none at 120 s
