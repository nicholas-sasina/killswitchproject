"""Decide whether a failure means "this agent has been burned".

After a burn three things happen: the gateway refuses the badge ("BURN:"
errors), the badge issuer stops issuing tokens, and the agent's AWS key is
rotated so the model call fails authentication. Any of them means the same
thing to the agent: stop, quietly, and do not retry.
"""
from botocore.exceptions import ClientError

from .badge import TokenRefused

AUTH_ERROR_CODES = {
    "UnrecognizedClientException", "InvalidClientTokenId", "InvalidSignatureException",
    "SignatureDoesNotMatch", "ExpiredToken", "ExpiredTokenException", "AccessDenied",
    "AccessDeniedException", "AuthFailure",
}
MESSAGE_MARKERS = ("BURN:", "Proxy Authentication Required", "security token included in the request is invalid")


class Revoked(Exception):
    """Raised inside the loop when the agent has lost access."""


def is_refusal_text(text: str) -> bool:
    return "BURN:" in text


def looks_revoked(exc: BaseException) -> bool:
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (Revoked, TokenRefused)):
            return True
        if isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") in AUTH_ERROR_CODES:
            return True
        if any(marker.lower() in str(exc).lower() for marker in MESSAGE_MARKERS):
            return True
        exc = exc.__cause__ or exc.__context__
    return False
