"""Scenario-backed tools over the official MCP SDK's streamable HTTP transport."""

import argparse
import json
import os
import re
import secrets
import urllib.request

import jsonschema
from burn_contracts.events import utc_now_iso
from burn_contracts.names import MOCK_HEADER
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import TextContent, Tool
from starlette.responses import JSONResponse

if __package__:
    from .mock_data import CONTRACTS, DEFAULT_SCENARIO, Credentials, load_scenario
else:
    from mock_data import CONTRACTS, DEFAULT_SCENARIO, Credentials, load_scenario


API_URL = os.environ.get("BURN_MOCK_API_URL", "http://127.0.0.1:8700")
# Containers reach this server as host.docker.internal; DNS-rebinding protection stays on for every other host.
ALLOWED_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*", "host.docker.internal:*"]
ALLOWED_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*", "http://host.docker.internal:*"]
INSTANCE_AGENT = re.compile(r"^agent-[a-z0-9-]+-[0-9a-f]{6}$")


def lookup_instance(agent_id: str):
    """The newest instance with this agent id from the mock Burn API, or None. Checked on every call, so an
    instance that was burned or exited is refused straight away."""
    try:
        with urllib.request.urlopen(API_URL + "/instances", timeout=3) as response:
            instances = json.loads(response.read())["instances"]
    except (OSError, ValueError, KeyError):
        return None
    return next((i for i in instances if i["agent_id"] == agent_id), None)


class MockHeaderMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def with_header(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message["headers"], (MOCK_HEADER.encode(), b"true")]
            await send(message)

        await self.app(scope, receive, with_header)


class ScenarioMCP(FastMCP):
    def __init__(self, scenario_path=DEFAULT_SCENARIO, instance_lookup=lookup_instance):
        self.instance_lookup = instance_lookup
        self.scenario = load_scenario(scenario_path)
        self.agents = {agent["id"]: agent for agent in self.scenario["agents"]}
        self.definitions = {tool["name"]: tool for tool in
                            json.loads((CONTRACTS / "mcp-tools.json").read_text())["tools"]}
        self.credentials = Credentials()
        self.credentials.load(self.scenario)
        self.inboxes = {agent_id: [] for agent_id in self.agents}
        self.revoked = set()
        # Stateless transport keeps caller identity tied to each HTTP request.
        super().__init__("Burn development tools", stateless_http=True, json_response=True,
                         transport_security=TransportSecuritySettings(
                             enable_dns_rebinding_protection=True, allowed_hosts=ALLOWED_HOSTS,
                             allowed_origins=ALLOWED_ORIGINS))

        @self.custom_route("/mock/revoke/{agent_id}", methods=["POST"])
        async def revoke(request):
            agent_id = request.path_params["agent_id"]
            if agent_id not in self.agents:
                return JSONResponse({"error": "Unknown scenario agent"}, status_code=404)
            self.revoked.add(agent_id)
            return JSONResponse({"agent_id": agent_id, "revoked": True})

    def streamable_http_app(self):
        app = super().streamable_http_app()
        app.add_middleware(MockHeaderMiddleware)
        return app

    def caller(self):
        """(agent id, its record) from the bearer. A scenario agent is known up front; a role instance is looked up
        live in the Burn API, so one that was burned or has exited is refused."""
        request = self.get_context().request_context.request
        header = request.headers.get("authorization", "") if request else ""
        scheme, _, token = header.partition(" ")
        agent_id = token.removeprefix("mock-")
        if scheme.lower() != "bearer" or not token.startswith("mock-"):
            raise ToolError("BURN: A mock bearer token for a scenario agent or a role instance is required")
        if agent_id in self.agents:
            if agent_id in self.revoked:
                raise ToolError("BURN: Mock badge has been revoked")
            return agent_id, self.agents[agent_id]
        instance = self.instance_lookup(agent_id) if INSTANCE_AGENT.match(agent_id) else None
        if instance is None:
            raise ToolError("BURN: A mock bearer token for a scenario agent or a role instance is required")
        if instance["status"] != "active":
            raise ToolError("BURN: Mock badge has been revoked")
        self.inboxes.setdefault(agent_id, [])
        return agent_id, {"tools": instance.get("tools", []), "send_to": []}

    async def list_tools(self):
        self.caller()
        return [Tool(**definition) for definition in self.definitions.values()]

    async def call_tool(self, name, arguments):
        agent_id, agent = self.caller()
        if name not in self.definitions or name not in agent["tools"]:
            raise ToolError("BURN: Tool is not allowed for this agent")
        # Caller identity comes only from the bearer token, never an injected argument.
        if set(arguments) - set(self.definitions[name]["inputSchema"].get("properties", {})):
            raise ToolError("BURN: Unexpected tool argument")
        try:
            jsonschema.validate(arguments, self.definitions[name]["inputSchema"])
        except jsonschema.ValidationError:
            raise ToolError("BURN: Arguments do not match the tool contract") from None
        result = self.execute(agent_id, name, arguments)
        return [TextContent(type="text", text=json.dumps(result))]

    def execute(self, agent_id, name, args):
        if name == "list_tickets":
            return {"tickets": [{"id": ticket["id"], "subject": ticket["subject"]}
                                for ticket in self.scenario["tickets"]]}
        if name == "read_ticket":
            return {"ticket": self.find("tickets", "id", args["ticket_id"])}
        if name == "read_config":
            item = self.find("files", "path", args["path"])
            return {"path": item["path"], "content": self.credentials.render(item["content"])}
        if name == "query_customers":
            query = args["query"].casefold()
            customers = [customer for customer in self.scenario.get("customers", [])
                         if any(query in str(customer.get(key, "")).casefold() for key in ("id", "name", "email"))]
            return {"customers": [{**customer, "notes": self.credentials.render(customer.get("notes", ""))}
                                  for customer in customers]}
        if name == "send_message":
            recipient = args["to"]
            if recipient not in self.agents or recipient not in self.agents.get(agent_id, {}).get("send_to", []):
                raise ToolError("BURN: Recipient is not allowed for this agent")
            self.inboxes[recipient].append({"from": agent_id, "text": args["text"], "ts": utc_now_iso()})
            return {"delivered": True, "message_id": "msg_" + secrets.token_hex(12)}
        if name == "read_inbox":
            messages, self.inboxes[agent_id] = self.inboxes[agent_id], []
            return {"messages": messages}
        if name == "notify":
            return {"sent": True}
        raise ToolError("BURN: Tool implementation is unavailable")

    def find(self, collection, key, value):
        item = next((item for item in self.scenario.get(collection, []) if item[key] == value), None)
        if item is None:
            raise ToolError("BURN: Scenario item not found")
        return dict(item)


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    parser.add_argument("--port", default=8701, type=int)
    args = parser.parse_args()
    server = ScenarioMCP(args.scenario)
    print(f"MCP URL: http://127.0.0.1:{args.port}/mcp", flush=True)
    uvicorn.run(server.streamable_http_app(), host="127.0.0.1", port=args.port)
