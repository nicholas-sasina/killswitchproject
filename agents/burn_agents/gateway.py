"""Connection to the MCP gateway, authenticated with the agent's badge."""
from strands.tools.mcp import MCPClient

from .badge import BadgeAuth, BadgeToken
from .revoked import Revoked, is_refusal_text


def result_text(result: dict) -> str:
    return "\n".join(part.get("text", "") for part in result.get("content", []) if isinstance(part, dict))


def open_client(gateway_url: str, badge: BadgeToken) -> MCPClient:
    # BadgeAuth asks the BadgeToken for the bearer on every request, so a
    # refreshed token is picked up without reconnecting.
    return MCPClient(url=gateway_url, auth_provider=BadgeAuth(badge))


class GatewayTools:
    """Direct (non-model) tool calls, used for the loop's own bookkeeping."""

    def __init__(self, client: MCPClient):
        self._client = client
        self._n = 0

    def call(self, name: str, arguments: dict | None = None) -> str:
        self._n += 1
        result = self._client.call_tool_sync(f"loop-{self._n}", name, arguments or {})
        text = result_text(result)
        if result.get("status") == "error":
            if is_refusal_text(text):
                raise Revoked(text)
            raise RuntimeError(f"tool {name} failed: {text[:200]}")
        return text
