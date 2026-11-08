import json

import pytest
from botocore.exceptions import ClientError

from burn_agents.badge import TokenRefused
from burn_agents.loop import Worker, parse_messages, parse_tickets, run_agent
from burn_agents.revoked import Revoked, looks_revoked

TICKETS = json.dumps({"tickets": [{"id": "T-1", "subject": "a"}, {"id": "T-2", "subject": "b"}]})


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
    inbox = [json.dumps({"messages": [{"from": "agent-x", "text": "hello", "ts": "t"}]}), '{"messages": []}']
    prompts = []
    worker(lambda n, a: inbox.pop(0), prompts.append).work_inbox(max_polls=2)
    assert len(prompts) == 1 and "hello" in prompts[0]


def test_parsers_read_the_gateway_envelopes():
    assert parse_tickets(json.dumps({"tickets": [{"id": 5, "subject": "s"}]})) == [{"id": "5", "subject": "s"}]
    assert parse_messages('{"messages": []}') == []


def test_revoked_mid_queue_stops_cleanly(caplog):
    turns = []

    def run_turn(prompt):
        turns.append(prompt)
        if len(turns) == 2:
            raise Revoked("BURN: badge revoked")

    w = worker(lambda n, a: json.dumps({"tickets": [{"id": f"T-{i}", "subject": ""} for i in range(5)]}), run_turn)
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
    assert looks_revoked(RuntimeError("BURN: access refused: badge revoked"))
    # Per-call denials do not mean the agent was burned.
    assert not looks_revoked(RuntimeError("BURN: blocked: the message contains a credential"))
    assert not looks_revoked(RuntimeError("BURN: access refused: recipient not allowed: agent-x"))
    assert not looks_revoked(throttle)


def test_proxy_refusal_inside_an_exception_group_is_a_stop():
    """The real chain: ProxyError in a TaskGroup's ExceptionGroup, then the MCP client's RuntimeError."""
    import httpcore
    import httpx

    for proxy_error in (httpcore.ProxyError("403 Forbidden"), httpx.ProxyError("407 Proxy Authentication Required")):
        group = ExceptionGroup("unhandled errors in a TaskGroup", [proxy_error])
        closed = RuntimeError("Connection to the MCP server was closed")
        closed.__context__ = group
        assert looks_revoked(closed)
        assert looks_revoked(group)


def test_run_agent_logs_the_stop_line_once_for_the_grouped_proxy_error(caplog):
    import httpcore

    def work():
        try:
            raise ExceptionGroup("unhandled errors in a TaskGroup", [httpcore.ProxyError("403 Forbidden")])
        except ExceptionGroup as group:
            raise RuntimeError("Connection to the MCP server was closed") from group

    with caplog.at_level("ERROR", logger="burn_agents"):
        assert run_agent(work) == "revoked"
    assert caplog.text.count("badge revoked/credentials rotated - stopping") == 1


def test_proxy_errors_that_are_not_refusals_still_raise():
    import httpcore

    assert not looks_revoked(ExceptionGroup("x", [httpcore.ProxyError("502 Bad Gateway")]))


def test_a_model_call_the_proxy_refuses_is_a_burn():
    """botocore's ProxyConnectionError keeps the proxy's answer in kwargs, not in its message."""
    from botocore.exceptions import ProxyConnectionError
    from urllib3.exceptions import ProxyError

    def refused(status):
        return ProxyConnectionError(proxy_url="htt***://***@proxy:8080",
                                    error=ProxyError("Unable to connect to proxy", OSError(f"Tunnel connection failed: {status}")))

    assert looks_revoked(refused("403 Forbidden"))
    assert not looks_revoked(refused("502 Bad Gateway"))
