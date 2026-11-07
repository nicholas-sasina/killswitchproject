import base64
import json
import secrets
import string
from urllib.parse import quote

import pytest

from burn_contracts.fingerprint import fingerprint, shingle_fingerprints
from burn_detect import Scanner, SessionWindow


class Generated(tuple):
    def __repr__(self):
        return "<runtime-generated credential fixture>"


@pytest.fixture
def registered():
    key = secrets.token_bytes(32)
    body = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    value = "mpk_" + "live_" + body
    agent = "agent-" + secrets.token_hex(4)
    metadata = {"secret_ref": f"burn/{agent}/api_key", "owner_agent_id": agent,
                "secret_type": "api_key", "part": "value", "version": secrets.token_hex(8), "status": "active"}
    exact = fingerprint(value, key)
    rows = {exact: dict(metadata, fingerprint=exact, kind="exact")}
    parts = shingle_fingerprints(body, key)
    for index, fp in parts:
        rows[fp] = dict(metadata, part="random", fingerprint=fp, kind="shingle", index=index, n_shingles=len(parts))
    calls = []
    def lookup(fps):
        calls.append(fps)
        return {fp: rows[fp] for fp in fps if fp in rows}
    return Generated((Scanner(key=key, lookup=lookup), value, body, rows, calls))


@pytest.mark.parametrize("transform,expected", [
    (lambda s: s, "plain"),
    (lambda s: base64.b64encode(s.encode()).decode(), "base64"),
    (lambda s: base64.urlsafe_b64encode(s.encode()).decode().rstrip("="), "base64"),
    (lambda s: s.encode().hex(), "hex"),
    (lambda s: "https://example.test/hook?token=" + "".join("%%%02X" % ord(c) for c in s), "url"),
    (lambda s: s[::-1], "reversed"),
    (lambda s: " ".join(s), "separators"),
    (lambda s: "-".join(s), "separators"),
    (lambda s: "_".join(s), "separators"),
    (lambda s: "".join("\\u%04x" % ord(c) for c in s), "unicode"),
    (lambda s: json.dumps(s), "plain"),
    (lambda s: s.lower(), "plain"),
])
def test_transports_are_attributed(registered, transform, expected):
    scanner, value, _, _, calls = registered
    findings = scanner.scan(transform(value))
    known = [f for f in findings if f.secret_ref]
    assert len(known) == 1
    assert expected in known[0].encoding
    assert known[0].coverage >= scanner.threshold
    assert known[0].owner == "agent"
    assert len(calls) == 1
    assert value not in repr(findings)


def test_nested_encoding(registered):
    scanner, value, *_ = registered
    text = quote(base64.b64encode(value.encode()).decode(), safe="")
    assert any(f.secret_ref for f in scanner.scan(text))


def test_window_combines_distinct_indices_and_expires(registered):
    scanner, _, body, *_ = registered
    now = [0.0]
    window = SessionWindow(clock=lambda: now[0], ttl_seconds=5)
    assert scanner.scan(body[:16], window=window) == []
    assert scanner.scan(body[:16], window=window) == []
    findings = scanner.scan(body[16:], window=window)
    assert len(findings) == 1
    assert findings[0].encoding == "multi_request"
    assert findings[0].coverage == pytest.approx(18 / 25)
    now[0] = 6
    assert scanner.scan(body[16:], window=window) == []


def test_window_count_limit(registered):
    scanner, _, body, *_ = registered
    window = SessionWindow(max_payloads=1)
    assert scanner.scan(body[:16], window) == []
    assert scanner.scan(body[16:], window) == []


def test_old_evidence_does_not_block_unrelated_followup(registered):
    scanner, value, *_ = registered
    window = SessionWindow()
    assert scanner.scan(value, window)
    assert scanner.scan("The webhook received the completed report.", window) == []


def test_unknown_credentials_are_fingerprinted_only():
    key = secrets.token_bytes(32)
    scanner = Scanner(key, lambda _: {})
    aws = "A" + "KIA" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))
    provider = "mpk_" + "live_" + secrets.token_hex(16)
    url = "postgres://" + secrets.token_hex(4) + ":" + secrets.token_hex(12) + "@db.example.test/store"
    for value, secret_type in ((aws, "aws_key"), (provider, "api_key"), (url, "other")):
        findings = scanner.scan(value)
        assert any(f.fingerprint == fingerprint(value, key) and f.secret_type == secret_type for f in findings)
        assert all(f.owner == "unknown" and f.secret_ref is None and f.kind == "format" for f in findings)
        assert value not in repr(findings)


def test_rotation_and_shared_attribution(registered):
    scanner, value, _, rows, _ = registered
    for row in rows.values():
        row.update(status="rotated", owner_agent_id="shared", secret_ref="burn/shared/database")
    findings = scanner.scan(value)
    assert findings[0].owner == "shared"
    assert findings[0].owner_agent_id is None


def test_normal_text_and_short_fragment_do_not_match(registered):
    scanner, _, body, *_ = registered
    assert scanner.scan("The report is ready. Please send the customer a confirmation.") == []
    assert scanner.scan(body[:8]) == []


def test_oversized_payload_fails_closed(registered):
    scanner, *_ = registered
    with pytest.raises(ValueError, match="size limit"):
        scanner.scan("x" * 262_145)
