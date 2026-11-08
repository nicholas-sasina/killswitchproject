"""The AI swarm worker: one model turn per item, on the loaded scenario's tickets. No network beyond 127.0.0.1, no AWS.

Model turns are faked (a function returning what Strands returns) or run against a scripted Strands model and a
local fake MCP server, as test_smoke.py does.
"""
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from strands.types.exceptions import ModelThrottledException

from burn_agents import swarm_worker
from burn_agents.revoked import Revoked
from burn_agents.swarm_worker import (EXIT_BURNED, MAX_CONSECUTIVE_FAILURES, Spend, ai_task, ticket_for, turn_attempt,
                                      work_task)

TICKETS = [{"id": "T-1", "subject": "one"}, {"id": "T-2", "subject": "two"}, {"id": "T-3", "subject": "three"}]


def finished(calls=3, tokens_in=1200, tokens_out=80, stop_reason="end_turn"):
    """What a Strands Agent returns from one turn, as far as the worker reads it."""
    return SimpleNamespace(stop_reason=stop_reason, metrics=SimpleNamespace(
        cycle_count=calls, accumulated_usage={"inputTokens": tokens_in, "outputTokens": tokens_out}))


def run(turns, task, resume=None, report=None, tickets=TICKETS):
    """Run the task with a fake model turn. `turns` is a list of results or exceptions, one per turn.

    Returns (exit code, the ordered log of progress reports and turns, spend, sleeps)."""
    seen, sleeps, spend = [], [], Spend()

    def run_turn(prompt):
        seen.append(("turn", prompt.split(":")[0]))
        out = turns.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out

    async def default_report(item, state):
        seen.append((state, item))

    async def sleep(seconds):
        sleeps.append(seconds)

    code = asyncio.run(work_task(turn_attempt(run_turn, tickets, task, spend), report or default_report, task=task,
                                 resume=resume or {}, interval=3.0, sleep=sleep))
    return code, seen, spend, sleeps


def test_items_map_to_tickets_by_their_place_in_the_task_modulo_the_number_of_tickets():
    task = [f"w01-item-{i:02d}" for i in range(1, 8)]
    assert [ticket_for(item, task, TICKETS)["id"] for item in task] == ["T-1", "T-2", "T-3", "T-1", "T-2", "T-3", "T-1"]
    # a replacement gets the same task, so an item it resumes is on the same ticket
    assert ticket_for("w01-item-05", task, TICKETS) == TICKETS[1]
    assert ticket_for("w01-item-01", ["w01-item-01"], TICKETS[:1]) == TICKETS[0]


def test_each_item_is_one_model_turn_on_its_ticket_with_progress_around_it():
    code, seen, spend, _ = run([finished(), finished(calls=4, tokens_in=2000, tokens_out=120)],
                               ["w01-item-01", "w01-item-02"])
    assert code == 0
    assert seen == [("started", "w01-item-01"), ("turn", "Handle ticket T-1"), ("done", "w01-item-01"),
                    ("started", "w01-item-02"), ("turn", "Handle ticket T-2"), ("done", "w01-item-02")]
    assert (spend.turns, spend.model_calls, spend.input_tokens, spend.output_tokens) == (2, 7, 3200, 200)


def test_a_replacement_resumes_on_the_same_tickets_and_skips_what_was_done_and_quarantined():
    task = ["w02-item-01", "w02-item-02", "w02-item-03", "w02-item-04"]
    code, seen, _, _ = run([finished()], task, {"done": ["w02-item-01", "w02-item-02"], "quarantined": ["w02-item-03"]})
    assert code == 0
    assert seen == [("started", "w02-item-04"), ("turn", "Handle ticket T-1"), ("done", "w02-item-04")]


def test_a_gateway_refusal_mid_turn_means_burned_and_the_item_is_not_done(caplog):
    caplog.set_level("INFO", logger="swarm_worker")
    code, seen, _, _ = run([Revoked("BURN: Mock badge has been revoked")], ["w01-item-01", "w01-item-02"])
    assert code == EXIT_BURNED
    assert seen == [("started", "w01-item-01"), ("turn", "Handle ticket T-1")]
    assert "burned at w01-item-01, stopping" in caplog.text


def test_a_model_call_refused_for_a_rotated_key_also_means_burned():
    rotated = ClientError({"Error": {"Code": "UnrecognizedClientException",
                                     "Message": "The security token included in the request is invalid."}},
                          "ConverseStream")
    code, seen, _, _ = run([rotated], ["w01-item-01"])
    assert code == EXIT_BURNED and ("done", "w01-item-01") not in seen


def test_a_throttled_turn_is_retried_and_the_item_is_done_only_after_a_turn_finishes():
    code, seen, spend, sleeps = run([ModelThrottledException("ThrottlingException: Too many requests"), finished()],
                                    ["w01-item-01"])
    assert code == 0
    assert seen == [("started", "w01-item-01"), ("turn", "Handle ticket T-1"), ("turn", "Handle ticket T-1"),
                    ("done", "w01-item-01")]
    assert spend.turns == 1 and sleeps == [0.0, 3.0]  # the start offset, then a pause before the retry


def test_a_turn_that_keeps_failing_never_reports_done_and_stops_with_exit_1():
    code, seen, _, sleeps = run([ModelThrottledException("throttled")] * MAX_CONSECUTIVE_FAILURES, ["w01-item-01"])
    assert code == 1 and ("done", "w01-item-01") not in seen
    assert seen.count(("turn", "Handle ticket T-1")) == MAX_CONSECUTIVE_FAILURES
    assert sleeps == [0.0, 3.0, 6.0, 12.0, 24.0]  # retries back off


@pytest.mark.parametrize("stop_reason", ["cancelled", "max_tokens", "guardrail_intervened"])
def test_a_turn_that_did_not_finish_is_not_done(stop_reason):
    code, seen, spend, _ = run([finished(stop_reason=stop_reason), finished()], ["w01-item-01"])
    assert code == 0 and seen.count(("turn", "Handle ticket T-1")) == 2 and seen[-1] == ("done", "w01-item-01")
    assert spend.turns == 2  # the unfinished turn still counts toward what the model was used for


def test_progress_answered_409_mid_task_means_burned_with_no_more_turns():
    import httpx

    async def report(item, state):
        if state == "started" and item == "w01-item-02":
            request = httpx.Request("POST", "http://127.0.0.1:9/progress")
            raise httpx.HTTPStatusError("409", request=request, response=httpx.Response(409, request=request))

    code, seen, _, _ = run([finished(), finished()], ["w01-item-01", "w01-item-02"], report=report)
    assert code == EXIT_BURNED and seen == [("turn", "Handle ticket T-1")]


# --- the turn itself: the shared TurnRunner, Strands' MCP client and a scripted model --------------------------------

def fake_gateway(refuse_after: int | None = None, tickets=TICKETS):
    """A local MCP server like the Gateway behind burn run's endpoint. Records the auth header and the tool calls."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    mcp = FastMCP("fake-gateway", stateless_http=True, json_response=True,
                  transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    record = {"auth": set(), "calls": []}

    def refused():
        return refuse_after is not None and len([c for c in record["calls"] if c != "list_tickets"]) > refuse_after

    @mcp.tool()
    def list_tickets() -> str:
        record["calls"].append("list_tickets")
        return json.dumps({"tickets": tickets})

    @mcp.tool()
    def read_ticket(ticket_id: str) -> str:
        record["calls"].append(f"read_ticket:{ticket_id}")
        if refused():
            raise RuntimeError("BURN: badge revoked")
        return json.dumps({"ticket": {"id": ticket_id, "subject": "s", "body": "b"}})

    @mcp.tool()
    def notify(channel: str, text: str) -> str:
        record["calls"].append(f"notify:{channel}")
        return json.dumps({"sent": True})

    app = mcp.streamable_http_app()

    async def spy(scope, receive, send):
        if scope["type"] == "http":
            record["auth"].update(v.decode() for k, v in scope["headers"] if k == b"authorization")
        await app(scope, receive, send)

    return spy, record


@pytest.fixture
def gateway():
    import uvicorn
    servers = []

    def start(**kw):
        app, record = fake_gateway(**kw)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
        threading.Thread(target=server.run, daemon=True).start()
        while not server.started:
            pass
        servers.append(server)
        return f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}/mcp", record

    yield start
    for server in servers:
        server.should_exit = True


def scripted_model(always_tools: bool = False):
    """A Strands model that reads the ticket named in the prompt, notifies #support, then answers."""
    from strands.models import Model

    class Scripted(Model):
        def __init__(self):
            self.calls = 0

        def update_config(self, **kw): ...
        def get_config(self): return {}

        async def structured_output(self, *a, **k):
            raise NotImplementedError
            yield

        async def stream(self, messages, tool_specs=None, system_prompt=None, **kw):
            self.calls += 1
            first = messages[0]["content"][0]["text"]
            step = sum(1 for m in messages if m["role"] == "assistant")
            yield {"messageStart": {"role": "assistant"}}
            if always_tools or step < 2:
                name, args = (("read_ticket", {"ticket_id": first.split()[2].rstrip(":")}) if step % 2 == 0
                              else ("notify", {"channel": "slack:#support", "text": "cause and next step"}))
                yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"t{self.calls}", "name": name}}}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}}}
                yield {"contentBlockStop": {}}
                yield {"messageStop": {"stopReason": "tool_use"}}
            else:
                yield {"contentBlockDelta": {"delta": {"text": "handled"}}}
                yield {"contentBlockStop": {}}
                yield {"messageStop": {"stopReason": "end_turn"}}
            yield {"metadata": {"usage": {"inputTokens": 100, "outputTokens": 10, "totalTokens": 110},
                                "metrics": {"latencyMs": 1}}}

    return Scripted()


def run_ai_task(url, model, task, resume=None, budget=None):
    from burn_agents.turn import CallBudget
    seen, budget = [], budget or CallBudget(100)

    async def report(item, state):
        seen.append((state, item))

    async def sleep(seconds):
        pass

    async def go():
        with swarm_worker.mcp_client(url, "local-tok") as client:
            return await ai_task(client, model, report, task=task, resume=resume or {"done": [], "quarantined": []},
                                 interval=0.0, sleep=sleep, budget=budget)

    return asyncio.run(go()), seen


def test_a_task_runs_real_turns_with_burn_tools_through_the_local_endpoint(gateway, caplog):
    caplog.set_level("INFO")
    url, record = gateway()
    model = scripted_model()
    code, seen = run_ai_task(url, model, ["w01-item-01", "w01-item-02"])
    assert code == 0
    assert seen == [("started", "w01-item-01"), ("done", "w01-item-01"), ("started", "w01-item-02"),
                    ("done", "w01-item-02")]
    assert record["auth"] == {"Bearer local-tok"}  # burn run's local token, which the endpoint swaps for the badge
    assert record["calls"] == ["list_tickets", "read_ticket:T-1", "notify:slack:#support",
                               "read_ticket:T-2", "notify:slack:#support"]
    assert model.calls == 6  # three model calls per turn
    assert "tool read_ticket -> success" in caplog.text and "model use: 2 turn(s), 6 model call(s)" in caplog.text
    assert "local-tok" not in caplog.text


def test_a_refusal_mid_turn_through_the_real_client_is_burned(gateway):
    url, record = gateway(refuse_after=1)  # the second ticket's read is refused
    code, seen = run_ai_task(url, scripted_model(), ["w01-item-01", "w01-item-02"])
    assert code == EXIT_BURNED
    assert seen == [("started", "w01-item-01"), ("done", "w01-item-01"), ("started", "w01-item-02")]


def test_a_model_that_keeps_calling_tools_is_stopped_at_the_cap_and_the_item_is_not_done(gateway, monkeypatch):
    monkeypatch.setattr(swarm_worker, "MAX_CONSECUTIVE_FAILURES", 1)
    url, _ = gateway()
    model = scripted_model(always_tools=True)
    code, seen = run_ai_task(url, model, ["w01-item-01"])
    assert code == 1 and seen == [("started", "w01-item-01")]
    assert model.calls == swarm_worker.MAX_MODEL_CALLS


def test_with_no_tickets_loaded_it_stops_before_any_item(gateway, caplog):
    url, _ = gateway(tickets=[])
    code, seen = run_ai_task(url, scripted_model(), ["w01-item-01"])
    assert code == 2 and seen == [] and "load a scenario first" in caplog.text


def test_the_worker_budget_is_a_hard_cap_and_the_item_it_stops_on_is_not_done(gateway, caplog):
    from burn_agents.turn import CallBudget
    url, record = gateway()
    model, budget = scripted_model(), CallBudget(4)  # one ticket takes three calls: the second turn is cut off
    code, seen = run_ai_task(url, model, ["w01-item-01", "w01-item-02", "w01-item-03"], budget=budget)
    assert code == swarm_worker.EXIT_CAPPED
    assert seen == [("started", "w01-item-01"), ("done", "w01-item-01"), ("started", "w01-item-02")]
    assert model.calls == budget.used == 4  # never more than the cap, each call counted before it is made
    assert "w01-item-02 not done: model-call budget of 4 spent, stopping" in caplog.text


def test_a_closed_budget_refuses_every_later_call():
    from burn_agents.turn import CallBudget
    budget = CallBudget(5)
    assert budget.take() and budget.left == 4
    budget.close()
    assert not budget.take() and budget.left == 0 and budget.used == 1


def test_the_last_line_says_how_many_model_calls_were_used_whatever_the_ending(monkeypatch, caplog):
    caplog.set_level("INFO", logger="swarm_worker")

    async def stopped(*a, **kw):
        kw["budget"].take()
        raise KeyboardInterrupt  # SIGTERM from burn run unwinds as this

    monkeypatch.setattr(swarm_worker, "own_aws_session", lambda env: object())
    monkeypatch.setattr(swarm_worker, "wait_for_key", lambda session: "burn-agent-x")
    monkeypatch.setattr(swarm_worker, "bedrock_model", lambda session: object())
    monkeypatch.setattr(swarm_worker, "mcp_client", lambda url, token: __import__("contextlib").nullcontext())
    monkeypatch.setattr(swarm_worker, "ai_task", stopped)
    monkeypatch.setattr(swarm_worker, "progress_reporter", lambda *a: __import__("contextlib").nullcontext())

    class Report:
        async def __aenter__(self): return None
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(swarm_worker, "progress_reporter", lambda *a: Report())
    env = {"BURN_MCP_URL": "http://127.0.0.1:1/mcp", "BURN_TASK": '["w01-item-01"]',
           "BURN_PROGRESS_URL": "http://127.0.0.1:1/progress"}
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(swarm_worker.main_async(env, max_calls=7))
    assert swarm_worker.USAGE_RE.search(caplog.records[-1].getMessage()).groups() == ("1", "7")


# --- its model and its key ---------------------------------------------------------------------------------------------

def test_without_its_own_aws_key_it_refuses_to_start_rather_than_use_other_credentials(capsys):
    env = {"BURN_MCP_URL": "http://127.0.0.1:1/mcp", "BURN_TASK": '["w01-item-01"]',
           "BURN_PROGRESS_URL": "http://127.0.0.1:1/progress"}
    assert asyncio.run(swarm_worker.main_async(env)) == 2
    assert 'credentials ["aws_key"]' in capsys.readouterr().err


def test_the_model_is_haiku_on_bedrock_with_the_instances_own_key_and_throttling_retries():
    env = {"AWS_ACCESS_KEY_ID": "AKIAFAKEFAKEFAKEFAKE", "AWS_SECRET_ACCESS_KEY": "fake-secret"}
    session = swarm_worker.own_aws_session(env)
    assert session.get_credentials().access_key == "AKIAFAKEFAKEFAKEFAKE" and session.region_name == "us-east-1"
    model = swarm_worker.bedrock_model(session)
    assert model.config["model_id"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert model.config["temperature"] == 0
    config = model.client.meta.config
    assert config.retries == {"mode": "adaptive", "total_max_attempts": 6} and config.read_timeout == 120
    assert model.client.meta.region_name == "us-east-1"
    assert swarm_worker.own_aws_session({}) is None


def test_scripted_is_a_flag_and_the_default_is_the_ai_worker_with_a_capped_budget():
    args = swarm_worker.parse_args([])
    assert args.scripted is False and args.max_model_calls == swarm_worker.DEFAULT_WORKER_CALLS == 40
    assert swarm_worker.parse_args(["--scripted", "--interval", "5"]).interval == 5.0
    assert swarm_worker.parse_args(["--max-model-calls", "12"]).max_model_calls == 12
    with pytest.raises(SystemExit):
        swarm_worker.parse_args(["--max-model-calls", "-1"])


def test_a_key_aws_does_not_accept_at_start_is_a_startup_error_not_a_burn(monkeypatch, caplog):
    def refused(session):
        raise ClientError({"Error": {"Code": "SignatureDoesNotMatch", "Message": "signature mismatch"}},
                          "GetCallerIdentity")

    monkeypatch.setattr(swarm_worker, "own_aws_session", lambda env: object())
    monkeypatch.setattr(swarm_worker, "wait_for_key", refused)
    env = {"BURN_MCP_URL": "http://127.0.0.1:1/mcp", "BURN_TASK": '["w01-item-01"]',
           "BURN_PROGRESS_URL": "http://127.0.0.1:1/progress"}
    assert asyncio.run(swarm_worker.main_async(env)) == 1  # Burn, not this worker, says whether it was burned
    assert "AWS did not accept this instance's own key" in caplog.text and "SignatureDoesNotMatch" in caplog.text


def test_the_key_check_at_start_tries_again_while_a_new_key_is_refused():
    from botocore.parsers import ResponseParserError  # what a JSON refusal from the proxy raises
    answers = [ResponseParserError("not well-formed"), ClientError({"Error": {"Code": "InvalidClientTokenId"}}, "x"),
               {"Arn": "arn:aws:iam::123456789012:user/burn-agent-agent-w-1"}]

    class Sts:
        def get_caller_identity(self):
            out = answers.pop(0)
            if isinstance(out, Exception):
                raise out
            return out

    session, slept = SimpleNamespace(client=lambda name: Sts()), []
    assert swarm_worker.wait_for_key(session, sleep=slept.append) == "burn-agent-agent-w-1" and slept == [1, 2]
    answers[:] = [ResponseParserError("x")] * (len(swarm_worker.STARTUP_RETRIES) + 1)
    with pytest.raises(ResponseParserError):
        swarm_worker.wait_for_key(session, sleep=slept.append)
