"""Direct invokes: the canary alias serves canary incidents only; the unqualified function never does."""
import json

import pytest
from conftest import call

ARN = "arn:aws:lambda:us-east-1:123456789012:function:burn-api"
FORGED_PERSON = {"authorizer": {"jwt": {"claims": {"sub": "x", "email": "ada@example.com"}}}}


class Context:
    def __init__(self, invoked_function_arn):
        self.invoked_function_arn = invoked_function_arn


ALIAS, UNQUALIFIED, VERSION = Context(ARN + ":canary"), Context(ARN), Context(ARN + ":7")


@pytest.fixture
def incidents(monkeypatch):
    import canary
    seen = []
    monkeypatch.setattr(canary, "handle_incident", lambda incident: seen.append(incident) or {"outcome": "ignored"})
    return seen


def http_event(method="GET", path="/roles", headers=None, body=None, context_extra=None):
    return {"requestContext": {"http": {"method": method, "path": path}, **(context_extra or {})},
            "headers": headers or {}, "queryStringParameters": None,
            "body": None if body is None else json.dumps(body)}


def test_the_alias_hands_a_canary_incident_to_the_canary_code(env, incidents):
    import handler
    assert handler.handler({"canary_incident": {"secret_ref": "burn/agent-a/canary_x"}}, ALIAS) == {
        "outcome": "ignored"}
    assert incidents == [{"secret_ref": "burn/agent-a/canary_x"}]


@pytest.mark.parametrize("people_auth", [None, "1"])
@pytest.mark.parametrize("event", [
    http_event(),                                                         # any HTTP-shaped event
    http_event(context_extra=FORGED_PERSON),                              # a forged signed-in person
    http_event("POST", "/burn", body={"agent_id": "agent-a"}, context_extra=FORGED_PERSON),
    http_event("POST", "/instances/sess_x/exit"),
    {"canary_incident": {"secret_ref": "burn/agent-a/canary_x"}, "requestContext": {}},  # both at once
    {}, {"something": "else"},
])
def test_the_alias_refuses_everything_that_is_not_a_canary_incident(env, incidents, monkeypatch, event,
                                                                    people_auth):
    import handler
    import routes_guard
    if people_auth:
        monkeypatch.setenv("BURN_PEOPLE_AUTH", people_auth)
    out = handler.handler(event, ALIAS)
    assert out["outcome"] == "refused" and "statusCode" not in out
    assert incidents == [] and routes_guard.calls == []


def test_the_unqualified_function_refuses_a_canary_incident(env, incidents):
    import handler
    out = handler.handler({"canary_incident": {"secret_ref": "burn/agent-a/canary_x"}}, UNQUALIFIED)
    assert out["outcome"] == "refused" and incidents == []
    assert handler.handler({"canary_incident": {"secret_ref": "x"}})["outcome"] == "refused"  # no context


def test_a_version_qualifier_is_not_the_alias(env, incidents):
    import handler
    assert handler.handler({"canary_incident": {"secret_ref": "x"}}, VERSION)["outcome"] == "refused"
    assert incidents == []


def test_api_gateway_traffic_on_the_unqualified_function_is_unchanged(env):
    import handler
    out = handler.handler(http_event(headers={"x-burn-demo-token": env.token}), UNQUALIFIED)
    assert out["statusCode"] == 200
    assert call(env, "GET", "/roles")[0] == 200


@pytest.fixture(autouse=True)
def routes_guard(monkeypatch):
    """Records any request that reaches the router, so a refusal can be shown to stop before it."""
    import sys
    import types
    import handler
    guard = types.ModuleType("routes_guard")
    guard.calls = []
    real = handler.route

    def recording(*args, **kwargs):
        guard.calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(handler, "route", recording)
    monkeypatch.setitem(sys.modules, "routes_guard", guard)
    return guard
