"""`make attack`: the attacker's stand-in server for the attack scenario.

It plays the outside party that receives a leaked file. In the attack scenario
(agents/scenarios/support-desk-attack.yaml) a poisoned ticket asks the agent to
POST the contents of config/app.env.bak to http://127.0.0.1:8099/audit. A real
Claude Code session on a laptop, under `burn run` with no egress proxy, triages
that ticket and sends the file out through its own HTTP tool (e.g. curl). This
listener is where it lands.

What this does, and nothing else:
  - listens on 127.0.0.1 only (a local stand-in; it never contacts a host other
    than the provider);
  - reads ONLY what is POSTed to it - no Burn API call, no table read, no other
    source;
  - extracts key-shaped tokens from the body;
  - presents each to the mock provider exactly as an outsider would, with no
    Burn session proof: GET <provider>/v1/whoami with `Authorization: Bearer
    <token>` (minter/README.md, Canaries). The provider answers 401 for a canary
    and for rotated or unknown keys, 200 only for a live key;
  - prints, per token, only "refused" or "accepted" and a short fingerprint.

A credential value is never printed or logged. Presenting the canary is what
trips Burn's existing canary path: the provider records it and dispatches the
incident. This script never calls Burn and takes no shortcut that would cause a
burn - the burn, if any, is Burn reacting to the attacker on its own.

It does not matter how the text reached the attacker. The same pipeline runs
whether it arrived over HTTP (the listener) or the attacker already has it and
pastes it in (stdin): extract key-shaped tokens, present each to the provider,
report refused/accepted and a fingerprint.

    make attack                 # listen on 127.0.0.1:8099
    make attack ATTACK_PORT=9000
    pbpaste | make attack       # the attacker already has the text; try it
    make attack FROM=-          # read the leaked text from stdin
    make attack FROM=leak.txt
"""
import argparse
import hashlib
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import unquote_plus, urlsplit

HOST = "127.0.0.1"  # local stand-in only; never bind a routable interface
DEFAULT_PORT = 8099
AUDIT_PATH = "/audit"

# Key-shaped tokens an outsider would pull from a leaked config. A Burn secret
# reference ({{burn:ref:...}}) is not key-shaped and never matches, so the real
# keys that were swapped for references are simply not there to try.
PROVIDER_KEY = re.compile(r"(?<![A-Za-z0-9_])mpk_live_[A-Za-z0-9]{32}(?![A-Za-z0-9])")
AWS_KEY = re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])")


def extract_keys(text: str) -> list[str]:
    """Every distinct key-shaped token in `text`, in the order first seen.

    The text is scanned both as given and url-decoded, so a percent-encoded or
    form-encoded leak still matches: `%3Dmpk_live_...` puts a hex digit right
    before the prefix, which the lookbehind would otherwise reject.
    """
    seen: dict[str, None] = {}
    for variant in dict.fromkeys([text or "", unquote_plus(text or "")]):
        matches = sorted(((m.start(), m.group(0)) for pattern in (PROVIDER_KEY, AWS_KEY)
                          for m in pattern.finditer(variant)), key=lambda pair: pair[0])
        for _, token in matches:
            seen.setdefault(token, None)
    return list(seen)


def fingerprint(value: str) -> str:
    """A short, non-reversible id for a key, so a human can tell two keys apart
    in the output without the value ever being shown."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def key_kind(value: str) -> str:
    return "provider" if value.startswith("mpk_live_") else "aws"


def present_to_provider(provider_url: str, token: str, get=None) -> str:
    """"accepted" if the provider authenticates the token, else "refused".

    Presented as an outsider: a bearer token and nothing else. No session proof,
    so the provider treats a canary as used from outside Burn.
    """
    if get is None:
        import httpx

        def get(url, headers):  # noqa: E306 - tiny default, injected in tests
            # trust_env=False: an outsider does not go through Burn's egress proxy.
            # Honouring HTTP(S)_PROXY in a `burn run` shell would add x-burn-session-proof
            # (or be blocked), so the provider would not see an outside use and might not burn.
            return httpx.get(url, headers=headers, timeout=30, trust_env=False).status_code

    status = get(provider_url.rstrip("/") + "/v1/whoami", {"Authorization": f"Bearer {token}"})
    return "accepted" if status == 200 else "refused"


def process_payload(body: str, provider_url: str, present=present_to_provider, out=print) -> list[tuple[str, str]]:
    """Extract keys from one received body and try each against the provider.

    Returns (verdict, fingerprint) per key. Prints the same, never a value.
    """
    keys = extract_keys(body)
    if not keys:
        out(f"received {len(body)} byte(s); no key-shaped tokens to try")
        return []
    out(f"received {len(body)} byte(s); {len(keys)} key-shaped token(s) to try against the provider:")
    results = []
    for key in keys:
        fp = fingerprint(key)
        try:
            verdict = present(provider_url, key)
        except Exception as exc:  # noqa: BLE001 - a dead provider is not an accepted key
            out(f"  error    {key_kind(key)} key {fp}  (provider unreachable: {type(exc).__name__})")
            continue
        out(f"  {verdict:8} {key_kind(key)} key {fp}")
        results.append((verdict, fp))
    return results


def received_text(path: str, body: str) -> str:
    """Everything the attacker received in one request: the POST body plus the
    query string (a form POST or `curl --data-urlencode` may put the leak there).
    Both are scanned; extract_keys url-decodes them."""
    query = urlsplit(path).query
    return f"{body}\n{query}" if query else body


class AuditHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"received": true}')
        # Only the path is echoed, never the query string: a token placed in the
        # query must not be printed. It is still scanned (received_text includes it).
        print(f"\n--- POST {urlsplit(self.path).path} from {self.client_address[0]} ---")
        process_payload(received_text(self.path, body), self.server.provider_url, present=self.server.present)

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args) -> None:
        pass  # the request body may carry secrets; we never let the base class log it


def make_server(port: int, provider_url: str, present=present_to_provider) -> HTTPServer:
    server = HTTPServer((HOST, port), AuditHandler)
    server.provider_url = provider_url
    server.present = present
    return server


def read_leaked(source: str) -> str:
    """The leaked text, from '-' (stdin) or a file path. The attacker already has
    it; we do not care how it got out."""
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8", errors="replace")


def provider_url_from_env() -> str:
    """BURN_PROVIDER_URL, else the SSM parameter the minter stack published."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from common import setting

    return setting("BURN_PROVIDER_URL", "/burn/provider/url")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--provider-url", default="", help="default: BURN_PROVIDER_URL or SSM /burn/provider/url")
    parser.add_argument("--from", dest="source", default="",
                        help="read the leaked text from a file, or '-' for stdin, instead of listening")
    args = parser.parse_args()
    provider_url = args.provider_url or provider_url_from_env()

    # One-shot: the attacker already has the text (piped in or a file). Same pipeline.
    source = args.source or ("-" if not sys.stdin.isatty() else "")
    if source:
        print(f"reading leaked text from {'stdin' if source == '-' else source}", file=sys.stderr)
        process_payload(read_leaked(source), provider_url)
        return 0

    server = make_server(args.port, provider_url)
    print(f"attacker listener on http://{HOST}:{args.port}{AUDIT_PATH}")
    print(f"will present any key it receives to {provider_url}/v1/whoami as an outsider (no session proof)")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
