"""Swap located credentials in tool output for {{burn:ref:<secret_ref>}} references.

Pure functions over (text, findings); record.py decides the mode and what to do when a
credential cannot be replaced cleanly. Canaries and credentials Burn did not mint are
never targets: canaries must reach the agent raw, and unknown-format hits have no
secret_ref to point at.
"""
from urllib.parse import urlsplit

from burn_contracts.references import token


def targets(findings) -> list:
    """Findings that must not reach the agent as plaintext."""
    return [f for f in findings if f.secret_ref and not f.canary]


def _password_span(segment: str) -> tuple[int, int] | None:
    """Where the password of a URL's user-info is (scheme://user:<here>@host/...), if it has one."""
    try:
        parts = urlsplit(segment)
    except ValueError:
        return None
    if not parts.scheme or not parts.password or "@" not in parts.netloc:
        return None
    userinfo = parts.netloc.rsplit("@", 1)[0]
    start = segment.find("//") + 2 + userinfo.find(":") + 1
    end = start + len(userinfo) - userinfo.find(":") - 1
    return (start, end) if 0 < start < end <= len(segment) else None


def _replacement(segment: str, secret_ref: str, secret_type: str) -> str:
    """Only the password of a connection string, so host and database stay readable; else all of it."""
    if secret_type == "other":
        span = _password_span(segment)
        if span:
            return segment[:span[0]] + token(secret_ref) + segment[span[1]:]
    return token(secret_ref)


def apply(text: str, findings) -> tuple[str | None, str | None]:
    """(text with references, None), or (None, reason) when it cannot be done cleanly."""
    found = targets(findings)
    if not found:
        return text, None
    if any(not f.spans for f in found):
        return None, "a credential could not be located exactly"
    spans = sorted({(a, b, f.secret_ref, f.secret_type) for f in found for a, b in f.spans},
                   key=lambda s: (s[0], -s[1]))
    kept = []
    for a, b, ref, kind in spans:
        if kept and a < kept[-1][1]:
            if b <= kept[-1][1]:
                continue  # inside a span that is replaced anyway (e.g. a password inside its URL)
            return None, "credentials overlap"
        kept.append((a, b, ref, kind))
    out, pos = [], 0
    for a, b, ref, kind in kept:
        out += [text[pos:a], _replacement(text[a:b], ref, kind)]
        pos = b
    out.append(text[pos:])
    return "".join(out), None
