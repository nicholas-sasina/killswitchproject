"""The provider adapter interface.

A provider turns an Incident into a request (`build`, pure and side-effect free, used
by tests and the dry run) and delivers it (`send`). Adding a destination type means
adding one module here and registering it in providers/__init__.py; the processor
does not change.

Errors are classified so the processor can decide what to do:
  transient  -> retry later (timeouts, connection errors, 5xx, 429)
  permanent  -> stop retrying (bad credential, unknown channel, invalid config/payload)
"""
from __future__ import annotations

import http.client
import json
import random
import ssl
import time
from dataclasses import dataclass, field
from typing import Any

from ..http import DEFAULT_TIMEOUT, Response, SafeTransport, Transport, UnsafeURL
from ..incident import Incident

MAX_RETRY_AFTER = 900.0  # never honour a Retry-After longer than SQS can sensibly hold a message


@dataclass(frozen=True)
class Destination:
    id: str
    provider: str
    enabled: bool = True
    min_severity: str = "high"
    categories: tuple[str, ...] = ()          # empty = all categories
    exclude_categories: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()              # empty = any agent
    recipient: str | None = None              # chat id / email list / channel label (no secrets)
    secret_ref: str | None = None
    digest_low: bool = False                  # low severity goes to a periodic digest
    rate_per_minute: int = 20
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreparedRequest:
    """Everything needed to deliver; `preview` is a redacted copy safe to print."""
    url_ref: str | None            # which secret holds the URL (never the URL itself)
    body: Any
    headers: dict[str, str] = field(default_factory=lambda: {"Content-Type": "application/json"})
    preview: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Ack:
    provider_ref: str | None = None  # message id etc., only when it is safe to store
    status: int | None = None


class DeliveryError(Exception):
    def __init__(self, category: str, *, retryable: bool, retry_after: float | None = None,
                 status: int | None = None):
        super().__init__(category)
        self.category = category
        self.retryable = retryable
        self.retry_after = None if retry_after is None else max(0.0, min(float(retry_after), MAX_RETRY_AFTER))
        self.status = status


def classify_http(resp: Response, *, retry_after: float | None = None) -> DeliveryError:
    """Map a non-success HTTP response to a DeliveryError (never includes the body)."""
    status = resp.status
    if status == 429:
        if retry_after is None:
            retry_after = _parse_retry_after(resp.header("retry-after"))
        return DeliveryError("rate_limited", retryable=True, retry_after=retry_after, status=status)
    if 300 <= status < 400:
        return DeliveryError("redirect_refused", retryable=False, status=status)
    if status in (401, 403):
        return DeliveryError("auth_rejected", retryable=False, status=status)
    if status in (404, 410):
        return DeliveryError("destination_not_found", retryable=False, status=status)
    if status in (400, 413, 422):
        return DeliveryError("payload_rejected", retryable=False, status=status)
    if status >= 500 or status in (408, 425):
        return DeliveryError("provider_unavailable", retryable=True, status=status)
    return DeliveryError("unexpected_status", retryable=False, status=status)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # HTTP-date form: fall back to backoff


def backoff(attempt: int, *, base: float = 1.0, cap: float = 60.0, rng=random.random) -> float:
    """Full-jitter exponential backoff, attempt starting at 1."""
    return rng() * min(cap, base * (2 ** max(0, attempt - 1)))


class Provider:
    name: str = "base"
    exact_hosts: frozenset[str] = frozenset()
    host_suffixes: tuple[str, ...] = ()
    path_prefixes: tuple[str, ...] = ("/",)
    timeout: float = DEFAULT_TIMEOUT

    def __init__(self, transport: Transport | None = None):
        self.transport = transport or SafeTransport(exact=self.exact_hosts, suffixes=self.host_suffixes,
                                                    path_prefixes=self.path_prefixes)

    # --- configuration -------------------------------------------------------------
    @classmethod
    def validate_destination(cls, dest: Destination) -> None:
        """Raise ValueError if a destination's (non-secret) config is unusable."""
        if not dest.secret_ref and cls.needs_secret:
            raise ValueError(f"destination {dest.id}: secret_ref is required")

    needs_secret = True

    # --- delivery ------------------------------------------------------------------
    def build(self, incident: Incident, dest: Destination) -> PreparedRequest:
        raise NotImplementedError

    def send(self, prepared: PreparedRequest, dest: Destination, secret: str | None) -> Ack:
        raise NotImplementedError

    # Shared helper for JSON webhooks.
    def _post_json(self, url: str, body: Any) -> Response:
        data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        try:
            return self.transport.post(url, data, {"Content-Type": "application/json"}, timeout=self.timeout)
        except UnsafeURL:
            raise DeliveryError("unsafe_url", retryable=False) from None
        except ssl.SSLCertVerificationError:
            # Untrusted or wrong-host certificate: recorded distinctly (possible interception), retried in
            # case it is transient, never bypassed.
            raise DeliveryError("tls_verification_failed", retryable=True) from None
        except (TimeoutError, ConnectionError, OSError):
            raise DeliveryError("network_error", retryable=True) from None
        except Exception as exc:
            # e.g. http.client.InvalidURL, whose message contains the request path (a webhook token).
            # Only the class name is kept; the exception is never chained, logged or stored.
            # ValueError (InvalidURL and friends) is a bad stored URL: permanent. Anything else is retried,
            # bounded by max receives and the DLQ.
            permanent = isinstance(exc, (ValueError, http.client.InvalidURL))
            raise DeliveryError(f"transport_error:{type(exc).__name__}"[:60], retryable=not permanent) from None


def sleep_for(seconds: float) -> None:  # patched in tests
    time.sleep(seconds)
