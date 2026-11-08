import asyncio
import json
import secrets
import socket
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
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
