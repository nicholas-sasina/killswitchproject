"""mitmproxy HTTP hooks. Do not enable flow dumps or request logging."""
import asyncio
import base64
from collections import OrderedDict
import json
import socket
import threading

from mitmproxy import http

from burn_contracts.names import is_agent_id
from burn_detect import Scanner, SessionWindow
from proxy.policy import ExitCheck, Settings, public_ip
from proxy.state import AwsState


class BurnProxy:
    def __init__(self, state=None, settings=None):
        self.state = state
        self.settings = settings or Settings.from_env()
        self.checker = None
        self.connections = {}
        self.windows = OrderedDict()
        self.lock = threading.Lock()

    def _initialize(self):
        if self.checker is None:
            self.state = self.state or AwsState()
            self.checker = ExitCheck(Scanner(self.state.key(), self.state.lookup), self.settings)

    def _reply(self, flow, reason, *, agent="system", session=None, finding=None, action="blocked"):
        fields = {"session_id": session, "detail": reason}
        host = flow.request.host.rstrip(".").lower()
        if host in self.settings.allowed_hosts or reason == "private_destination":
            fields["destination"] = host
        if finding:
            fields.update({name: getattr(finding, name) for name in (
                "secret_ref", "fingerprint", "secret_type", "owner", "owner_agent_id", "encoding")})
        event_id = self.state.emit(agent_id=agent, action=action, **fields)
        flow.response = http.Response.make(403, json.dumps({"blocked": True, "reason": reason, "event_id": event_id}),
                                           {"content-type": "application/json"})

    def _identity(self, flow):
        auth = flow.request.headers.pop("proxy-authorization", None)
        if auth is None:
            return self.connections.get(flow.client_conn.id)
        try:
            kind, value = auth.split(" ", 1)
            if kind.lower() != "basic":
                return None
            agent, session = base64.b64decode(value, validate=True).decode().split(":", 1)
            if not is_agent_id(agent) or not session or len(session) > 512:
                return None
            return agent, session
        except (ValueError, UnicodeError):
            return None

    def _process(self, flow, connect=False, headers_only=False):
        with self.lock:
            try:
                self._initialize()
                identity = self._identity(flow)
                if not identity:
                    self._reply(flow, "session_required", action="refused")
                    return
                agent, session = identity
                session_row = self.state.session(agent, session)
                if not session_row:
                    # An unrecognized auth password is never written to the event stream.
                    self._reply(flow, "session_inactive", agent=agent, action="refused")
                    return
                self.connections[flow.client_conn.id] = identity
                host, port = flow.request.host.rstrip(".").lower(), flow.request.port
                reason = self.settings.route_reason(host, port) or self.settings.role_reason(host, session_row)
                if reason:
                    self._reply(flow, reason, agent=agent, session=session)
                    return
                addresses = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
                if not addresses or not self.settings.is_sink(host, port) and not all(public_ip(r[4][0]) for r in addresses):
                    self._reply(flow, "private_destination", agent=agent, session=session)
                    return
                if connect:
                    return
                if headers_only:
                    length = flow.request.headers.get("content-length", "0")
                    if not length.isdecimal() or int(length) > 262_144:
                        self._reply(flow, "payload_too_large", agent=agent, session=session)
                    else:
                        reason = self.settings.request_reason(flow.request.url, flow.request.method,
                            int(length) > 0 or "transfer-encoding" in flow.request.headers,
                            list(flow.request.headers.items(multi=True)))
                        if reason:
                            self._reply(flow, reason, agent=agent, session=session)
                    return
                if session not in self.windows:
                    self.windows[session] = SessionWindow()
                self.windows.move_to_end(session)
                while len(self.windows) > 4096:
                    self.windows.popitem(last=False)
                body = ""
                if not self.settings.in_boundary(host, flow.request.scheme):
                    body = flow.request.get_content().decode("utf-8")
                decision = self.checker.check(url=flow.request.url,
                    headers=list(flow.request.headers.items(multi=True)), body=body,
                    agent_id=agent, window=self.windows[session], method=flow.request.method)
                if decision.reason:
                    self._reply(flow, decision.reason, agent=agent, session=session, finding=decision.finding)
            except Exception:
                # Exceptions can embed a URL or body. Return only a constant, never a traceback.
                flow.response = http.Response.make(403, b'{"blocked":true,"reason":"exit_check_unavailable"}',
                                                   {"content-type": "application/json"})

    async def http_connect(self, flow):
        await asyncio.to_thread(self._process, flow, True)

    async def requestheaders(self, flow):
        await asyncio.to_thread(self._process, flow, False, True)

    async def request(self, flow):
        if flow.response is None:
            await asyncio.to_thread(self._process, flow)

    def client_disconnected(self, client):
        self.connections.pop(client.id, None)

    async def server_connect(self, data):
        host, port = data.server.address
        host = host.rstrip(".").lower()
        try:
            if self.settings.route_reason(host, port):
                raise ValueError("destination rejected")
            addresses = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
            ips = {row[4][0] for row in addresses}
            if not ips or not self.settings.is_sink(host, port) and not all(public_ip(ip) for ip in ips):
                raise ValueError("destination rejected")
            # Pin the checked address to prevent a second DNS answer from bypassing the check.
            data.server.address = (addresses[0][4][0], port)
            data.server.sni = host
        except Exception:
            data.server.error = "destination rejected"


addons = [BurnProxy()]
