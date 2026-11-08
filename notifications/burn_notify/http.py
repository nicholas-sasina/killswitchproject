"""Outbound HTTPS with SSRF guards.

* HTTPS only, port 443 only, no userinfo, host must match the provider's allowlist.
* The host is resolved once; every address must be public (no private, loopback,
  link-local, multicast, reserved or metadata ranges). The connection is then pinned
  to a vetted address with SNI + certificate checks against the real hostname, so a
  DNS answer cannot change between the check and the connect (rebinding).
* No redirects are followed (a 3xx is returned as a permanent error).
* Request and response bodies are bounded; connect + read share a timeout.

Tests replace the transport with a fake, so no unit test opens a socket.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from dataclasses import dataclass, field
from typing import Callable, Protocol
from urllib.parse import urlsplit

MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
DEFAULT_TIMEOUT = 5.0


class UnsafeURL(ValueError):
    """The URL failed validation. The message never contains the URL (it may hold a token)."""


@dataclass
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


class Transport(Protocol):
    def post(self, url: str, body: bytes, headers: dict[str, str], *, timeout: float) -> Response: ...


def host_allowed(host: str, exact: frozenset[str], suffixes: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    return host in exact or any(host.endswith(s) and len(host) > len(s) for s in suffixes)


def check_url(url: str, *, exact: frozenset[str] = frozenset(), suffixes: tuple[str, ...] = (),
              path_prefixes: tuple[str, ...] = ("/",)) -> tuple[str, str]:
    """Return (host, path_with_query) for an allowed URL, else raise UnsafeURL."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeURL("malformed URL") from exc
    if parts.scheme != "https":
        raise UnsafeURL("only https URLs are allowed")
    if parts.username or parts.password or "@" in parts.netloc:
        raise UnsafeURL("credentials in URL authority are not allowed")
    if port not in (None, 443):
        raise UnsafeURL("only port 443 is allowed")
    host = (parts.hostname or "").lower()
    if not host or _is_ip_literal(host):
        raise UnsafeURL("an IP-literal or empty host is not allowed")
    if not host_allowed(host, exact, suffixes):
        raise UnsafeURL("host is not an approved provider host")
    if parts.fragment:
        raise UnsafeURL("fragments are not allowed")
    path = parts.path or "/"
    if not any(path.startswith(p) for p in path_prefixes):
        raise UnsafeURL("path is not an approved webhook path")
    return host, path + (f"?{parts.query}" if parts.query else "")


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def is_public_address(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                                 or ip.is_reserved or ip.is_unspecified)


Resolver = Callable[[str], list[str]]


def system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to a vetted IP, verifies TLS against the original hostname."""

    def __init__(self, host: str, ip: str, timeout: float, context: ssl.SSLContext):
        super().__init__(host, 443, timeout=timeout, context=context)
        self._ip = ip
        self._ctx = context

    def connect(self):  # noqa: D401 - http.client API
        sock = socket.create_connection((self._ip, 443), self.timeout)
        self.sock = self._ctx.wrap_socket(sock, server_hostname=self.host)


class SafeTransport:
    """Real transport used in Lambda. Construct with the provider's allowlist."""

    def __init__(self, *, exact: frozenset[str] = frozenset(), suffixes: tuple[str, ...] = (),
                 path_prefixes: tuple[str, ...] = ("/",), resolver: Resolver = system_resolver):
        self.exact, self.suffixes, self.path_prefixes = exact, suffixes, path_prefixes
        self.resolver = resolver
        self.context = ssl.create_default_context()

    def vet(self, url: str) -> tuple[str, str, str]:
        host, path = check_url(url, exact=self.exact, suffixes=self.suffixes, path_prefixes=self.path_prefixes)
        try:
            addresses = self.resolver(host)
        except OSError as exc:
            raise ConnectionError("DNS resolution failed") from exc
        if not addresses or not all(is_public_address(a) for a in addresses):
            raise UnsafeURL("host resolves to a non-public address")
        return host, path, addresses[0]

    def post(self, url: str, body: bytes, headers: dict[str, str], *, timeout: float = DEFAULT_TIMEOUT) -> Response:
        if len(body) > MAX_REQUEST_BYTES:
            raise UnsafeURL("request body too large")
        host, path, ip = self.vet(url)
        conn = _PinnedHTTPSConnection(host, ip, timeout, self.context)
        try:
            conn.request("POST", path, body=body, headers={**headers, "Host": host,
                                                          "User-Agent": "burn-notify/1.0"})
            resp = conn.getresponse()
            data = resp.read(MAX_RESPONSE_BYTES + 1)[:MAX_RESPONSE_BYTES]
            return Response(resp.status, {k.lower(): v for k, v in resp.getheaders()}, data)
        finally:
            conn.close()
