"""An agent stopped mid-MCP-call (by a burn) goes away while the local MCP endpoint is still writing to it. That must
be quiet: no exception escapes the handler, so nothing prints a traceback under burn run's own stop line."""
import socket
import struct
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from burn_cli.runner.badge import BadgeToken
from burn_cli.runner.credentials import Badge
from burn_cli.runner.mcp_proxy import McpProxy
from fake_servers import issuer

CHUNKS, GAP = 40, 0.025  # the Gateway streams for about a second


def slow_gateway(done: threading.Event):
    """A Gateway that streams server-sent events slowly, and says when it has sent the last one."""
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length") or 0))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            try:
                for i in range(CHUNKS):
                    self.wfile.write(f"data: {i}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(GAP)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the endpoint hung up on us once its caller was gone: it stops reading, as it should
            finally:
                done.set()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_a_client_that_goes_away_mid_stream_raises_nothing(capfd):
    done, badge = threading.Event(), issuer()
    gateway = slow_gateway(done)
    proxy = McpProxy(f"http://127.0.0.1:{gateway.server_port}/mcp",
                     BadgeToken(Badge("a", "s", badge.url + "/token")), local_token="loc").start()
    raised = []
    proxy._server.handle_error = lambda request, address: raised.append(sys.exc_info()[1])  # what would print
    try:
        port = int(proxy.url.split(":")[2].split("/")[0])
        sock = socket.create_connection(("127.0.0.1", port))
        sock.sendall(b"POST /mcp HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer loc\r\nContent-Length: 2\r\n\r\n{}")
        assert b"data: 0" in sock.recv(4096) or b"data: 0" in sock.recv(4096)  # the stream has started
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))  # reset, as a killed agent's
        sock.close()
        assert done.wait(10)  # the Gateway finished, so the endpoint has written past the disconnect
        time.sleep(0.3)
        assert raised == []
        err = capfd.readouterr().err
        assert "Traceback" not in err and "BrokenPipeError" not in err and "ConnectionResetError" not in err
        # and the endpoint keeps serving the next caller
        request = urllib.request.Request(proxy.url, data=b"{}", headers={"Authorization": "Bearer loc"})
        with urllib.request.urlopen(request, timeout=10) as reply:
            assert reply.status == 200 and b"data: 39" in reply.read()
    finally:
        proxy.stop()
        gateway.shutdown()
        gateway.server_close()
        badge.close()
