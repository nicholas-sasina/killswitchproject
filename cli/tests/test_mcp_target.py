"""The local MCP endpoint only ever sends the badge token to the Gateway."""
import pytest

from burn_cli.runner.badge import BadgeToken
from burn_cli.runner.credentials import Badge
from test_run import stack  # noqa: F401  (fixture)


# -- the badge token only goes to the Gateway -------------------------------------------------------------
@pytest.mark.parametrize("target", [".evil.example/mcp", "@evil.example/mcp", "evil/mcp",
                                    "http://evil.example/mcp", ":80/mcp", ""])
def test_mcp_endpoint_refuses_request_targets_that_could_leave_the_gateway(stack, target):
    import socket
    from burn_cli.runner.mcp_proxy import McpProxy
    proxy = McpProxy(stack.gw.url + "/mcp", BadgeToken(Badge("a", "s", stack.badge.url)), local_token="loc").start()
    try:
        port = int(proxy.url.split(":")[2].split("/")[0])
        with socket.create_connection(("127.0.0.1", port)) as sock:
            sock.sendall(f"POST {target} HTTP/1.0\r\nAuthorization: Bearer loc\r\nContent-Length: 0\r\n\r\n".encode())
            reply = sock.makefile("rb").read()
        assert reply.startswith(b"HTTP/1.0 400") or reply.startswith(b"HTTP/1.1 400") or b" 400 " in reply.split(b"\r\n")[0]
        assert not stack.gw.seen and not stack.badge.state["clients"]  # no token was even minted, let alone sent
    finally:
        proxy.stop()


def test_mcp_url_building_keeps_the_gateway_host():
    from burn_cli.runner.mcp_proxy import McpProxy
    proxy = McpProxy("https://gw.example.com/mcp", BadgeToken(Badge("a", "s", "http://x")), local_token="t")
    assert proxy._upstream_url("/mcp?x=1") == "https://gw.example.com/mcp?x=1"
    assert proxy._upstream_url("/") == "https://gw.example.com/"
    assert proxy._upstream_url("//evil.com/mcp") is None and proxy._upstream_url("@evil.com/") is None
    assert proxy._upstream_url(".evil.com/mcp") is None and proxy._upstream_url("/mcp") == "https://gw.example.com/mcp"
    proxy._server.server_close()
