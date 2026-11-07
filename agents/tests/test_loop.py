import json

import pytest
from botocore.exceptions import ClientError

from burn_agents.badge import TokenRefused
from burn_agents.loop import Worker, parse_messages, parse_tickets, run_agent
from burn_agents.revoked import Revoked, looks_revoked

TICKETS = json.dumps([{"id": "T-1", "subject": "a"}, {"id": "T-2", "subject": "b"}])


def worker(call_tool, run_turn, **kw):
    return Worker(call_tool, run_turn, lambda: None, sleep=lambda s: None, repeat=False, **kw)


def test_ticket_agent_runs_one_turn_per_ticket():
    prompts = []
    worker(lambda name, args: TICKETS, prompts.append).work_tickets()
    assert [p.split(":")[0] for p in prompts] == ["Handle ticket T-1", "Handle ticket T-2"]


def test_gate_is_waited_on_before_each_ticket():
    events = []
    w = worker(lambda n, a: TICKETS, lambda p: events.append("turn"), gate=lambda: events.append("gate"))
    w.work_tickets()
    assert events == ["gate", "turn", "gate", "turn"]


def test_inbox_agent_runs_a_turn_per_message():
    inbox = [json.dumps([{"from": "agent-x", "text": "hello"}]), "[]"]
    prompts = []
    worker(lambda n, a: inbox.pop(0), prompts.append).work_inbox(max_polls=2)
    assert len(prompts) == 1 and "hello" in prompts[0]


def test_parsers_tolerate_wrapped_and_plain_forms():
    assert parse_tickets(json.dumps({"tickets": [{"id": 5, "subject": "s"}]})) == [{"id": "5", "subject": "s"}]
    assert parse_messages("") == []
    assert parse_messages('{"messages": []}') == []
    assert parse_messages("just text")[0]["text"] == "just text"


def test_revoked_mid_queue_stops_cleanly(caplog):
    turns = []

    def run_turn(prompt):
        turns.append(prompt)
        if len(turns) == 2:
            raise Revoked("BURN: badge revoked")

    w = worker(lambda n, a: json.dumps([{"id": f"T-{i}", "subject": ""} for i in range(5)]), run_turn)
    with caplog.at_level("ERROR", logger="burn_agents"):
        assert run_agent(w.work_tickets) == "revoked"
    assert len(turns) == 2
    assert "badge revoked/credentials rotated - stopping" in caplog.text


def test_unrelated_errors_are_not_swallowed():
    def boom():
        raise ValueError("bug")

    with pytest.raises(ValueError):
        run_agent(boom)


def test_looks_revoked_recognises_each_signal():
    auth = ClientError({"Error": {"Code": "UnrecognizedClientException", "Message": "x"}}, "Converse")
    throttle = ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "Converse")
    wrapped = RuntimeError("model failed")
    wrapped.__cause__ = auth
    assert looks_revoked(auth) and looks_revoked(wrapped)
    assert looks_revoked(TokenRefused("x"))
    assert looks_revoked(RuntimeError("BURN: revoked"))
    assert not looks_revoked(throttle)
