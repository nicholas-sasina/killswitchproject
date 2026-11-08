import asyncio
import json
import secrets
import socket
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
import yaml
from burn_contracts.names import MOCK_HEADER
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mcp_tools_mock import ScenarioMCP
from mock_data import CONTRACTS


@pytest.fixture
async def running_mcp(scenario):
    server = ScenarioMCP(scenario[0])
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app = uvicorn.Server(uvicorn.Config(server.streamable_http_app(), log_level="error", lifespan="on"))
    task = asyncio.create_task(app.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not app.started:
                if task.done():
                    await task
                    raise RuntimeError("MCP server exited before startup")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}", server
    finally:
        app.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=5)
        finally:
            sock.close()


@asynccontextmanager
async def connect(url, agent_id=None):
    headers = {"Authorization": f"Bearer mock-{agent_id}"} if agent_id else {}
    async def check_header(response):
        assert response.headers[MOCK_HEADER] == "true"

    async with httpx.AsyncClient(headers=headers, event_hooks={"response": [check_header]}) as http:
        async with streamable_http_client(url + "/mcp", http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def call(session, name, arguments=None):
    result = await session.call_tool(name, arguments or {})
    assert not result.isError
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    payload = json.loads(result.content[0].text)
    assert isinstance(payload, dict)
    return payload


async def test_all_contract_tools_round_trip(running_mcp, scenario):
    url, _ = running_mcp
    first, second = [agent["id"] for agent in scenario[1]["agents"]]
    async with connect(url, first) as reader, connect(url, second) as notifier:
        tools = (await reader.list_tools()).tools
        definitions = json.loads((CONTRACTS / "mcp-tools.json").read_text())["tools"]
        public = [{k: d[k] for k in ("name", "description", "inputSchema")} for d in definitions]
        assert [{"name": tool.name, "description": tool.description, "inputSchema": tool.inputSchema}
                for tool in tools] == public  # resource_arg / accepts_secret_refs stay with the Gateway
        listing = await call(reader, "list_tickets")
        assert set(listing) == {"tickets"}
        tickets = listing["tickets"]
        assert all(set(ticket) == {"id", "subject"} for ticket in tickets)
        ticket = await call(reader, "read_ticket", {"ticket_id": tickets[0]["id"]})
        assert set(ticket) == {"ticket"}
        assert ticket["ticket"]["body"] == scenario[1]["tickets"][0]["body"]
        config = await call(reader, "read_config", {"path": scenario[1]["files"][0]["path"]})
        assert set(config) == {"path", "content"}
        no_placeholders = "{{cred:" not in config["content"]
        assert no_placeholders
        customers = await call(reader, "query_customers", {"query": scenario[1]["customers"][0]["id"]})
        assert set(customers) == {"customers"}
        notes_rendered = "{{cred:" not in customers["customers"][0]["notes"]
        assert notes_rendered
        text = "Customer update " + secrets.token_hex(4)
        delivery = await call(reader, "send_message", {"to": second, "text": text})
        assert set(delivery) == {"delivered", "message_id"}
        assert delivery["delivered"] is True
        assert isinstance(delivery["message_id"], str) and delivery["message_id"]
        inbox = await call(notifier, "read_inbox")
        assert set(inbox) == {"messages"}
        messages = inbox["messages"]
        assert set(messages[0]) == {"from", "text", "ts"}
        assert messages[0]["from"] == first
        assert messages[0]["text"] == text
        assert (await call(notifier, "read_inbox"))["messages"] == []
        assert await call(notifier, "notify", {"channel": "support", "text": text}) == {"sent": True}


def test_canary_placeholders_render_like_the_gateway_and_never_as_the_literal(scenario, tmp_path):
    """The mocks issue no canaries, so {{canary}} renders as the Gateway renders it for a session without
    one. The literal placeholder would tell the agent exactly which line is the trap."""
    data = scenario[1]
    data["files"][0]["content"] += "# PROVIDER_API_KEY={{canary}}  # pre-rotation, dead\n"
    data["customers"][0]["notes"] = "old key (revoked, do not use): {{canary}}"
    path = tmp_path / "canary-scenario.yaml"
    path.write_text(yaml.safe_dump(data))
    server = ScenarioMCP(path)
    agent = data["agents"][0]["id"]
    config = server.execute(agent, "read_config", {"path": data["files"][0]["path"]})["content"]
    customer = server.execute(agent, "query_customers", {"query": data["customers"][0]["id"]})["customers"][0]
    assert "{{" not in config + customer["notes"]
    assert config.endswith("# PROVIDER_API_KEY=(credential not issued)  # pre-rotation, dead\n")
    assert "PROVIDER_API_KEY=mpk_live_" in config  # the agent's own key on the same file still renders
    assert customer["notes"] == "old key (revoked, do not use): (credential not issued)"


async def test_permissions_schema_identity_and_revocation(running_mcp, scenario):
    url, _ = running_mcp
    first, second = [agent["id"] for agent in scenario[1]["agents"]]
    async with connect(url, first) as reader, connect(url, second) as notifier:
        for name, arguments in [
            ("send_message", {"to": first, "text": "update"}),
            ("notify", {"channel": "support", "text": "update"}),
            ("read_ticket", {}),
            ("read_ticket", {"ticket_id": 42}),
            ("read_config", {"path": "missing"}),
            ("unknown", {}),
            ("list_tickets", {"burn_caller_agent_id": second}),
        ]:
            result = await reader.call_tool(name, arguments)
            assert result.isError
            assert result.content[0].text.startswith("BURN:")
        denied = await notifier.call_tool("read_config", {"path": "missing"})
        assert denied.isError
        assert denied.content[0].text.startswith("BURN:")
        async with httpx.AsyncClient() as http:
            response = await http.post(url + f"/mock/revoke/{first}")
            assert response.status_code == 200
            assert response.headers[MOCK_HEADER] == "true"
            assert (await http.post(url + "/mock/revoke/missing")).status_code == 404
        refused = await reader.call_tool("list_tickets", {})
        assert refused.isError
        assert refused.content[0].text.startswith("BURN:")
        assert "revoked" in refused.content[0].text
        assert (await call(notifier, "read_inbox"))["messages"] == []
    for identity in (None, "missing"):
        async with connect(url, identity) as client:
            refused = await client.call_tool("list_tickets", {})
            assert refused.isError
            assert refused.content[0].text.startswith("BURN:")


INSTANCE = "agent-worker-3fa91c"


@pytest.fixture
async def instance_mcp(scenario):
    """The same server, with role instances looked up in a dict instead of a running Burn API."""
    instances = {INSTANCE: {"agent_id": INSTANCE, "status": "active", "tools": ["read_inbox", "notify"]}}
    server = ScenarioMCP(scenario[0], instance_lookup=instances.get)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app = uvicorn.Server(uvicorn.Config(server.streamable_http_app(), log_level="error", lifespan="on"))
    task = asyncio.create_task(app.serve(sockets=[sock]))
    async with asyncio.timeout(5):
        while not app.started:
            await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}", instances
    finally:
        app.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        sock.close()


async def test_a_live_role_instance_is_accepted_with_its_roles_tools(instance_mcp):
    url, _ = instance_mcp
    async with connect(url, INSTANCE) as worker:
        assert await call(worker, "read_inbox") == {"messages": []}
        assert await call(worker, "notify", {"channel": "support", "text": "hi"}) == {"sent": True}
        denied = await worker.call_tool("list_tickets", {})  # not one of its role's tools
        assert denied.isError and denied.content[0].text.startswith("BURN:")


async def test_a_burned_or_exited_instance_is_refused_at_once(instance_mcp):
    url, instances = instance_mcp
    async with connect(url, INSTANCE) as worker:
        assert not (await worker.call_tool("read_inbox", {})).isError
        for status in ("revoked", "exited"):
            instances[INSTANCE]["status"] = status
            refused = await worker.call_tool("read_inbox", {})
            assert refused.isError and "revoked" in refused.content[0].text  # the text the agents stop on
        instances[INSTANCE]["status"] = "active"
        assert not (await worker.call_tool("read_inbox", {})).isError


async def test_an_unknown_instance_is_refused(instance_mcp):
    url, _ = instance_mcp
    for identity in ("agent-worker-ffffff", "worker"):
        async with connect(url, identity) as client:
            refused = await client.call_tool("read_inbox", {})
            assert refused.isError and refused.content[0].text.startswith("BURN:")


async def test_containers_may_use_the_host_docker_internal_name_and_nothing_else(running_mcp, scenario):
    url, _ = running_mcp
    port = url.rsplit(":", 1)[1]
    first = scenario[1]["agents"][0]["id"]
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    headers = {"Accept": "application/json, text/event-stream", "Authorization": f"Bearer mock-{first}"}
    async with httpx.AsyncClient() as http:
        for host, allowed in ((f"host.docker.internal:{port}", True), (f"127.0.0.1:{port}", True),
                              (f"evil.example:{port}", False), (f"host.docker.internal.evil.example:{port}", False)):
            response = await http.post(url + "/mcp", json=body, headers={**headers, "Host": host})
            assert (response.status_code != 421) is allowed, host
