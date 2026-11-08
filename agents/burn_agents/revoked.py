"""Decide whether a failure means "this agent has been burned".

After a burn four things happen: the gateway refuses the badge ("BURN:"
errors), the badge issuer stops issuing tokens, the agent's AWS key is
rotated so the model call fails authentication, and the egress proxy refuses
the agent's session. Any of them means the same
thing to the agent: stop, quietly, and do not retry.
"""
import httpcore
import httpx
from botocore.exceptions import ClientError, ProxyConnectionError

from .badge import TokenRefused

AUTH_ERROR_CODES = {
    "UnrecognizedClientException", "InvalidClientTokenId", "InvalidSignatureException",
    "SignatureDoesNotMatch", "ExpiredToken", "ExpiredTokenException", "AccessDenied",
    "AccessDeniedException", "AuthFailure",
    # The egress proxy's JSON refusal of a burned session on a connection already open: botocore finds no AWS error
    # code in it and uses the bare HTTP status. AWS's own refusals always carry a name like AccessDenied.
    "403",
}
BADGE_MARKERS = ("revoked", "unknown badge", "badge issuer refused")
MESSAGE_MARKERS = ("Proxy Authentication Required", "security token included in the request is invalid")


class Revoked(Exception):
    """Raised inside the loop when the agent has lost access."""


def is_refusal_text(text: str) -> bool:
    """True only for "your badge is dead" refusals.

    The gateway also answers "BURN: access refused: recipient not allowed" and
    "BURN: blocked: ..." for single calls. Those leave the agent alive: the
    model sees the error and carries on.
    """
    return "BURN:" in text and any(m in text.lower() for m in BADGE_MARKERS)


def is_proxy_refusal(exc: BaseException) -> bool:
    """The egress proxy refuses a burned agent's session (HTTP 403, or 407 for bad proxy auth)."""
    proxy_errors = (httpx.ProxyError, httpcore.ProxyError, ProxyConnectionError)
    # botocore keeps the proxy's answer out of its message, in kwargs["error"]
    text = f"{exc} {getattr(exc, 'kwargs', {}).get('error', '')}"
    return isinstance(exc, proxy_errors) and any(code in text for code in ("403", "407"))


def looks_revoked(exc: BaseException) -> bool:
    """Walk the cause/context chain and into exception groups.

    The MCP client runs its transport in an asyncio TaskGroup, so a refused
    proxy CONNECT arrives wrapped in an ExceptionGroup inside a RuntimeError.
    """
    pending, seen = [exc], set()
    while pending:
        exc = pending.pop()
        if exc is None or id(exc) in seen:
            continue
        seen.add(id(exc))
        if isinstance(exc, (Revoked, TokenRefused)) or is_proxy_refusal(exc):
            return True
        if isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") in AUTH_ERROR_CODES:
            return True
        if is_refusal_text(str(exc)) or any(m.lower() in str(exc).lower() for m in MESSAGE_MARKERS):
            return True
        pending += [exc.__cause__, exc.__context__, *getattr(exc, "exceptions", ())]
    return False
