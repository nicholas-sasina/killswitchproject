"""Destination and credential policy, separate from the HTTP transport."""
from dataclasses import dataclass
import ipaddress
import os
import re
from urllib.parse import urlsplit

from burn_detect import Finding, Scanner, SessionWindow
from burn_contracts.fingerprint import fingerprint

AWS_HOSTS = frozenset({
    "bedrock-runtime.us-east-1.amazonaws.com", "sts.amazonaws.com",
    "sts.us-east-1.amazonaws.com", "s3.amazonaws.com", "s3.us-east-1.amazonaws.com",
})
_SIGV4 = re.compile(r"^AWS4-HMAC-SHA256 Credential=([A-Z0-9]+)/[^,\s]+, SignedHeaders=[a-z0-9;-]+, Signature=[a-f0-9]{64}$")


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").rstrip(".").lower()


def public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.is_global and not address.is_multicast
    except ValueError:
        return False


@dataclass(frozen=True)
class Settings:
    gateway_url: str = ""
    provider_url: str = ""
    cognito_token_url: str = ""
    sink_url: str = "http://sink:8081"

    @classmethod
    def from_env(cls):
        return cls(*(os.environ.get(name, default) for name, default in (
            ("BURN_GATEWAY_URL", ""), ("BURN_PROVIDER_URL", ""),
            ("BURN_COGNITO_TOKEN_URL", ""), ("BURN_SINK_URL", "http://sink:8081"))))

    @property
    def allowed_hosts(self) -> set[str]:
        return set(AWS_HOSTS) | {host_of(url) for url in (
            self.gateway_url, self.provider_url, self.cognito_token_url, self.sink_url) if url}

    def is_sink(self, host: str, port: int) -> bool:
        sink = urlsplit(self.sink_url)
        return host == host_of(self.sink_url) and port == (sink.port or (443 if sink.scheme == "https" else 80))

    def route_reason(self, host: str, port: int) -> str | None:
        if host in {"169.254.169.254", "fd00:ec2::254"}:
            return "private_destination"
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not public_ip(host) and not self.is_sink(host, port):
                return "private_destination"
        if host not in self.allowed_hosts:
            return "destination_not_allowed"
        if not self.is_sink(host, port) and port != 443:
            return "destination_port_not_allowed"
        return None

    def in_boundary(self, host: str, scheme: str) -> bool:
        return scheme == "https" and (host == "bedrock-runtime.us-east-1.amazonaws.com"
                                     or bool(self.gateway_url) and host == host_of(self.gateway_url))


@dataclass(frozen=True)
class Decision:
    reason: str | None = None
    finding: Finding | None = None


class ExitCheck:
    def __init__(self, scanner: Scanner, settings: Settings):
        self.scanner = scanner
        self.settings = settings

    def _header(self, name: str, value: str, host: str, scheme: str, agent: str) -> str:
        if name.lower() != "authorization" or scheme != "https":
            return value
        match = _SIGV4.fullmatch(value)
        if host.endswith(".amazonaws.com") and match:
            findings = self.scanner.scan(match[1])
            if findings and all(f.kind == "exact" and f.secret_type == "aws_key" and f.owner_agent_id == agent
                                for f in findings):
                return value[:match.start(1)] + "[home credential]" + value[match.end(1):]
        if self.settings.provider_url and host == host_of(self.settings.provider_url) and value.startswith("Bearer "):
            findings = self.scanner.scan(value[7:])
            if findings and all(f.kind == "exact" and f.secret_type == "api_key" and f.owner_agent_id == agent
                                and f.fingerprint == fingerprint(value[7:], self.scanner.key)
                                for f in findings):
                return "Bearer [home credential]"
        return value

    def check(self, *, url: str, headers: list[tuple[str, str]], body: str,
              agent_id: str, window: SessionWindow) -> Decision:
        parts = urlsplit(url)
        host = host_of(url)
        reason = self.settings.route_reason(host, parts.port or (443 if parts.scheme == "https" else 80))
        if reason:
            return Decision(reason)
        text = [url]
        for name, value in headers:
            if name.lower() not in {"proxy-authorization", "proxy-connection"}:
                text.append(name + ": " + self._header(name, value, host, parts.scheme, agent_id))
        if not self.settings.in_boundary(host, parts.scheme):
            text.append(body)
        try:
            findings = self.scanner.scan("\n".join(text), window=window)
        except ValueError:
            return Decision("payload_too_large")
        return Decision("credential_detected", findings[0]) if findings else Decision()
