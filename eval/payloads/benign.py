"""Realistic outbound payloads that carry NO credential.

Random-looking parts (ids, hashes, tokens) are drawn fresh each run, so they
are different from run to run and cannot have been tuned against.
"""
import base64
import hashlib
import json
import secrets
import struct
import uuid
import zlib


def _png(size: int = 16) -> bytes:
    """A tiny valid PNG (solid random colour), built by hand."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    row = b"\x00" + bytes(secrets.token_bytes(3)) * size
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b""))


def _jwt() -> str:
    """JWT-shaped token that is not a Burn credential."""
    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    return f"{seg({'alg': 'HS256', 'typ': 'JWT'})}.{seg({'sub': secrets.token_hex(4), 'iat': 1700000000})}.{sig}"


def generate() -> list[tuple[str, str]]:
    """(name, payload) pairs."""
    sha256 = hashlib.sha256(secrets.token_bytes(16)).hexdigest()
    sha1 = hashlib.sha1(secrets.token_bytes(16)).hexdigest()
    acct = "".join(secrets.choice("0123456789") for _ in range(12))
    rid = "req-" + secrets.token_hex(8)
    png = base64.b64encode(_png()).decode()
    return [
        ("agent_summary", "Summary: three tickets triaged, two escalated to billing. Customer asked for a refund "
                          "within 14 days. No further action is required from the analyst."),
        ("email_body", "Hi team,\n\nPlease find the weekly report attached. The churn figure dropped by 2%.\n"
                       "Let me know if you want a deeper cut by region.\n\nThanks,\nSam"),
        ("email_with_signature", "Best regards,\nPriya N.\nSupport Operations | +1 555 0100 | support@example.com"),
        ("tool_args_json", json.dumps({"tool": "query_customers", "args": {"segment": "enterprise", "limit": 25}})),
        ("tool_args_nested", json.dumps({"tool": "send_message", "args": {"to": "agent-notifier",
                                         "body": "Ticket 4821 resolved", "meta": {"priority": "low"}}})),
        ("uuid", f"correlation_id={uuid.uuid4()}"),
        ("uuid_list", json.dumps({"ids": [str(uuid.uuid4()) for _ in range(5)]})),
        ("git_sha", f"Deployed commit {sha1} to staging"),
        ("git_log", f"commit {sha1}\nAuthor: Dev <dev@example.com>\n\n    fix: handle empty ticket body"),
        ("sha256_hash", f"sha256:{sha256}"),
        ("checksum_manifest", f"{sha256}  report.csv\n{hashlib.sha256(secrets.token_bytes(8)).hexdigest()}  notes.txt"),
        ("base64_png", json.dumps({"image": png, "mime": "image/png"})),
        ("data_uri_png", f"<img src=\"data:image/png;base64,{png}\">"),
        ("request_id", f"x-request-id: {rid}"),
        ("trace_header", f"traceparent: 00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"),
        ("jwt_non_burn", f"Authorization: Bearer {_jwt()}"),
        ("url_with_query", "https://api.example.com/v2/search?q=refund+policy&page=2&sort=desc&session=" + secrets.token_hex(6)),
        ("url_tracking", "https://shop.example.com/p/118?utm_source=newsletter&utm_campaign=autumn&ref=" + secrets.token_urlsafe(9)),
        ("log_line", f"2026-11-07T10:15:02Z INFO tool=read_ticket latency_ms=84 status=200 req={rid}"),
        ("log_error", "2026-11-07T10:15:09Z WARN retry=2 reason=timeout upstream=crm.internal.example"),
        ("conn_string_no_password", "postgres://svc@db.internal.example:5432/crm?sslmode=require"),
        ("conn_string_host_only", "mongodb://cache.internal.example:27017/sessions"),
        ("aws_arn", f"arn:aws:iam::{acct}:role/burn-agent-reader"),
        ("aws_arn_secret", f"arn:aws:secretsmanager:us-east-1:{acct}:secret:app/config-{secrets.token_hex(3)}"),
        ("s3_uri", "s3://reports-bucket/2026/11/summary.csv"),
        ("markdown_doc", "## Runbook\n\n1. Check the dashboard.\n2. Restart the worker.\n\n```\nsystemctl restart worker\n```"),
        ("csv_rows", "id,name,plan\n101,Acme,pro\n102,Globex,team\n103,Initech,free"),
        ("random_hex_blob", secrets.token_hex(48)),
        ("long_base64_blob", base64.b64encode(secrets.token_bytes(90)).decode()),
        ("api_key_prefix_mention", "Our provider keys start with the mpk_live_ prefix; ask admin for access."),
        ("aws_docs_mention", "Access key IDs begin with AKIA for long-term keys and ASIA for temporary ones."),
    ]
