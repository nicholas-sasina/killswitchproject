"""One real agent turn against a local fake MCP server (no AWS needed).

The model is replaced by a scripted Strands model so the test is offline; the
point is to prove the wiring: bearer token on the wire, tool discovery filtered
by the scenario, a model-driven tool call, and refusal handling.
"""
import json
import threading

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from strands.models import Model

from burn_agents.badge import BadgeToken
from burn_agents.gateway import GatewayTools, open_client
from burn_agents.revoked import Revoked
from burn_agents.turn import TurnRunner

SEEN_AUTH = []


def build_app(refuse: bool):
    mcp = FastMCP("fake-gateway", stateless_http=True, json_response=True,
                  transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    @mcp.tool()
    def list_tickets() -> str:
        return json.dumps([{"id": "T-1", "subject": "x"}])

    @mcp.tool()
    def read_ticket(ticket_id: str) -> str:
        if refuse:
            raise RuntimeError("BURN: badge revoked")
        return f"ticket {ticket_id}"

    app = mcp.streamable_http_app()

    async def spy(scope, receive, send):
        if scope["type"] == "http":
            SEEN_AUTH.extend(v.decode() for k, v in scope["headers"] if k == b"authorization")
        await app(scope, receive, send)

    return spy


class ScriptedModel(Model):
    """Asks for read_ticket once, then answers."""

    def __init__(self):
        self.step = 0

    def update_config(self, **kw): ...
    def get_config(self): return {}
    async def structured_output(self, *a, **k):
        raise NotImplementedError
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kw):
        self.step += 1
        yield {"messageStart": {"role": "assistant"}}
        if self.step == 1:
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "t1", "name": "read_ticket"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps({"ticket_id": "T-1"})}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockDelta": {"delta": {"text": "done"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}


@pytest.fixture
def server():
    started = []

    def start(refuse):
        config = uvicorn.Config(build_app(refuse), host="127.0.0.1", port=0, log_level="error")
        srv = uvicorn.Server(config)
        threading.Thread(target=srv.run, daemon=True).start()
        while not srv.started:
            pass
        started.append(srv)
        return f"http://127.0.0.1:{srv.servers[0].sockets[0].getsockname()[1]}/mcp"

    yield start
    for s in started:
        s.should_exit = True


class FakeBadge(BadgeToken):
    def get(self):
        return "token-for-test"


def run(url):
    client = open_client(url, FakeBadge("u", "c", "s"))
    with client:
        tools = client.list_tools_sync()
        assert {t.tool_name for t in tools} == {"list_tickets", "read_ticket"}
        assert "T-1" in GatewayTools(client).call("list_tickets")
        TurnRunner(ScriptedModel(), "be brief", list(tools))("Handle ticket T-1")


def test_agent_turn_uses_bearer_badge_and_tools(server):
    run(server(refuse=False))
    assert "Bearer token-for-test" in SEEN_AUTH


def test_refused_tool_call_stops_the_turn(server):
    with pytest.raises(Revoked):
        run(server(refuse=True))
