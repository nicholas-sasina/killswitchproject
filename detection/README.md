# Detection

## What it does
Detects credentials in tool output and blocks outbound leaks through an authenticated HTTP/HTTPS proxy.
Includes a content-free webhook sink and an operator CLI for on-demand exit checks.

## How it works
- `Scanner(key=..., lookup=...)` uses only the Python standard library and `burn_contracts`.
- Normalization, exact fingerprints and 60% shingle coverage detect plain, encoded and split credentials.
- The proxy checks sessions every second, refreshes fingerprints every two seconds and writes safe events.
- Only approved destinations pass; Bedrock and gateway bodies and credentials in their own service headers follow the trust boundary.
- The sink records only size and SHA-256; the operator CLI reads credentials privately and sends them through the proxy.

## Key files
- `burn_detect/scanner.py`: `Scanner` and immutable `Finding` with the shared interface.
- `burn_detect/normalize.py`: bounded decoding and candidate extraction.
- `burn_detect/window.py`: per-session shingle evidence with count and time limits.
- `tests/test_scanner.py`: freshly generated credential tests independent of external evaluation.
- `proxy/addon.py`, `proxy/policy.py`, `proxy/state.py`: mitmproxy hooks, trust boundary and AWS caches.
- `sink/__main__.py`: partner webhook, optional TLS and receipt metadata.
- `leaktest/service.py`, `leaktest/__main__.py`: operator exit-check CLI.
- `scripts/proxy_env.py`: private `.run/proxy.env` from `burn-system/egress-proxy-key`.
- `scripts/test_containment.py`, `docker-compose.test.yml`: disposable isolated-network proof.
- `../infra/burn_infra/egress.py`: least-privilege proxy identity and its Secrets Manager entry.

## How to test it
```bash
cd detection
uv run --python 3.12 pytest -q
uv run --python 3.12 python scripts/benchmark.py
uv run --python 3.12 --extra proxy --extra leaktest pytest -q
python3 scripts/test_containment.py
```

The first command tests the stdlib library; optional service tests skip without their extras.
The Docker check generates fresh credentials and TLS certificates in disposable volumes and uses a
local AWS emulator. It verifies that direct internet and direct sink access fail, benign HTTPS works,
all seven credential transports are blocked, split evidence accumulates across requests, and HTTP
and HTTPS CONNECT to metadata are refused. It removes only its own test containers and volumes.
Both `docker compose` and the standalone `docker-compose` command work.

Test the IAM template separately:
```bash
cd ../infra
uv run --python 3.12 pytest -q ../detection/tests/test_egress_infra.py
```

Library integration:
```python
from burn_detect import Scanner, SessionWindow

# lookup accepts set[str] and returns {fingerprint: burn_fingerprints_row}.
scanner = Scanner(key=fingerprint_key_bytes, lookup=lookup)
findings = scanner.scan(text, window=SessionWindow())
```

Reuse one window per session. A `Finding` contains `secret_ref`, `fingerprint`, `secret_type`,
`owner`, `owner_agent_id`, `kind`, `encoding`, and `coverage`. Rotated rows are checked too.
Shingle evidence is grouped by secret, part and version, so unrelated parts never add together.
Windows retain matched indices and fingerprint metadata for eight requests within 30 seconds,
without retaining payloads. Prior evidence alone does not block an unrelated request.

Deploy the proxy's identity, then prepare its local environment and image context:
```bash
cd infra
uv sync
npx aws-cdk@2 deploy BurnEgress --require-approval never
cd ../detection
uv run --python 3.12 --extra proxy python scripts/proxy_env.py
python3 scripts/prepare_build.py
```

The environment writer uses the default AWS credential chain to retrieve the proxy's dedicated
access key; it writes only to gitignored `.run/proxy.env` with mode 600. Gateway, provider and
Cognito token URLs come from environment variables or SSM parameters `/burn/gateway/url`,
`/burn/provider/url`, `/burn/identity/token-url`. Missing URLs are omitted from the allowlist.
Re-run the writer when a service URL changes. All AWS clients use `us-east-1`.

Use build context `.run/detection-build` relative to the repository root, with Dockerfiles
`detection/proxy/Dockerfile` and `detection/sink/Dockerfile`. The context preparer copies only
non-ignored product files, excluding local environments and certificates even with the classic
Docker builder. Re-run it after changing source files. A Compose proxy service needs:
```yaml
build:
  context: ../.run/detection-build
  dockerfile: detection/proxy/Dockerfile
env_file: ../.run/proxy.env
ports: ["127.0.0.1:8080:8080"]
networks: [agents_internal, egress]
volumes: ["certs:/certs"]
```

Attach agents only to an `internal: true` network and the sink only to the separate egress network.
The proxy is attached to both. Set agents' `HTTP_PROXY` and `HTTPS_PROXY` to
`http://${AGENT_ID}:${SESSION_ID}@proxy:8080`; mount the certificate volume read-only and point
`SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE` and `AWS_CA_BUNDLE` at `/certs/mitmproxy-ca-cert.pem`.
mitmproxy generates the CA at container startup. Do not enable flow dumps or verbose request logs.

The allowlist contains Bedrock runtime, STS, global/regional S3 and the configured gateway,
provider, Cognito token and sink hosts. Except for the explicitly configured sink endpoint,
all destinations must use port 443 and resolve exclusively to public addresses. Metadata is
always refused. DNS answers are checked and the outgoing connection is pinned to a checked IP.
The sink exception supports Docker's private service address; it does not open an entire subnet.

Run an operator exit check from the repository root after starting the real proxy and sink:
```bash
make exit-check-test AGENT="$AGENT_ID" ENCODING=base64
```

The command loads `.env`, uses the proxy CA at `.run/proxy-certs/mitmproxy-ca-cert.pem`, and
prints blocked/allowed with the reason. Encoding defaults to `random` when omitted.
Set `BURN_PROXY_URL` (default `http://localhost:8080`) and `BURN_SINK_URL` (default `http://sink:8081`)
in `.env`. The host resolves the sink through the proxy, so its Docker hostname works.
The tool uses the default AWS chain to read the selected agent's ledger, current session and
Secrets Manager value. If `BURN_API_URL` is set it reads the ledger through that API using
`BURN_DEMO_TOKEN`; otherwise it queries `burn-exposures`. Only credentials in that ledger are
eligible. Free text is sent unchanged. Results include no payload or raw credential.
The tool prefers the latest active session, falling back to the latest session of any status so
a burned agent's attempt reaches the proxy and returns `blocked=true` with `session_inactive`.
An agent without a session, an invalid encoding or an unavailable credential produces a sanitized
CLI error without printing the payload or credential.

## FAQ
**Why 60% coverage?** A 32-character random body has 25 shingles. Splitting it into two halves
reveals 18 distinct shingles (72%); one eight-character fragment reveals only 4%. The threshold
allows useful split-request detection without treating every short fragment as a full credential.

**Does this catch every leak?** No. Novel transforms, very small fragments, short credentials
split into small pieces and decoding beyond two layers can escape detection. The outbound host
allowlist is the backstop. Payloads larger than 256 KiB are refused; binary requests outside the
trust boundary fail closed. Runtime work is bounded to 96 variants. WebSockets and raw TCP
tunnels are disabled. A session revocation can take up to one second to reach the proxy's cache.
The 10 KiB prose benchmark takes about 4 ms locally; high-entropy input takes about 42 ms,
so the 20 ms target is not met for every payload. These timings exclude remote lookups.

**What can be recovered from storage or logs?** The detector reports only keyed fingerprints
and attribution. Unknown-format credentials have `secret_ref=None` and `owner=unknown`.
The sink stores only byte counts and SHA-256 digests, never paths, headers or bodies. The proxy
does not log payloads, and dependency errors return a constant reason without exception details.
`burn_detect` itself has only stdlib and shared `burn_contracts` imports and can be bundled in Lambda.

## Destination restrictions

Both S3 endpoints accept only HTTPS `GET /`, without a query string or request body.
This permits the demo's signed `ListBuckets` request and refuses object reads, uploads,
deletes and bucket-specific requests. The proxy checks the method and body framing before
forwarding a request, then checks the full request again. Bucket subdomains are not globally
allowlisted.

The swarm session's proposed `egress` field narrows the deployment allowlist: missing or
`null` uses the legacy defaults, `[]` denies all, and a list permits only those exact hosts
that are also globally allowed. The session owner supplies this field; the proxy does not
look up a mutable role separately. Invalid policies fail closed. This adapter follows
`ws/swarms-contracts`; it takes effect when the session writer supplies the field.

The evaluation configuration audit counts the simulated-attacker sink as outsider-readable.
Custom Gateway/provider endpoints require owner verification rather than being declared safe
from a hostname alone. A local configuration audit does not prove remote access controls.
