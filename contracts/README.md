# contracts

## What it does
The source of truth every other folder codes against: event format, API shapes, MCP tool list,
scenario format, and the shared naming/fingerprint rules below.
A contract change updates this folder and the mocks together.

## How it works
- JSON Schema for data (`ledger-event.schema.json`, `scenario.schema.json`), OpenAPI for APIs.
- `python/burn_contracts/` is a tiny stdlib-only helper (event ids, event builder, fingerprints,
  resource names) so every component builds events and fingerprints the same way.
- Lambdas get `burn_contracts` bundled in by `infra/build_assets.py`.

## Key files
| File | What |
|---|---|
| `ledger-event.schema.json` | One ledger/dashboard event |
| `burn-api.openapi.yaml` | Public Burn API (dashboard, runner, eval) |
| `minter.openapi.yaml` | Internal minter API (direct Lambda invoke) |
| `leaktest.openapi.yaml` | Local leak-test tool |
| `mcp-tools.json` | MCP tools behind the Gateway |
| `scenario.schema.json` | `/agents/scenarios/*.yaml` format |
| `python/burn_contracts/` | Shared helpers: `ids.py`, `events.py`, `fingerprint.py`, `names.py` |

## How to test it
```bash
cd contracts && uv run --with pytest --with jsonschema --with pyyaml pytest -q
```

## FAQ
- **Why contracts first?** Every component is built and tested against these files and a mock of
  every API, not against each other, so pieces can be developed and replaced independently.
- **Do you store credentials in the ledger?** No. The ledger stores an HMAC-SHA256 fingerprint keyed
  with a server-side secret, so the ledger itself is useless to an attacker.

---

## Conventions

### Names
- Agent ids: `^agent-[a-z0-9-]{1,40}$`. They come from the scenario file, never from code.
- Session ids are opaque strings issued by `POST /sessions`.
- Region: `us-east-1` only. All resource names are fixed (below) so stacks reference each other by
  name instead of CloudFormation exports. See `python/burn_contracts/names.py`.

### Secrets Manager layout
| Secret name | Value | Written by |
|---|---|---|
| `burn/<agent_id>/api_key` | raw mock-provider key | minter |
| `burn/<agent_id>/aws_key` | JSON `{"access_key_id","secret_access_key","iam_user"}` | minter |
| `burn/<agent_id>/badge` | JSON `{"client_id","client_secret","token_url","scope"}` | burn_api (sessions) |
| `burn/shared/<name>` | raw value rendered from the scenario template | minter |
| `burn-system/fingerprint-key` | 64 hex chars, HMAC key for fingerprints | infra (BurnData) |
| `burn-system/demo-token` | token for the `x-burn-demo-token` header | infra (BurnData) |

Rotation keeps the previous value at version stage `AWSPREVIOUS`. That is how the demo
replays the pre-rotation key without anyone having stored a copy.

### Credential formats
| type | Format | Notes |
|---|---|---|
| `api_key` | `mpk_live_` + 32 chars `[A-Za-z0-9]` (`secrets.choice`) | accepted by the mock API provider only while current |
| `aws_key` | a real IAM access key on IAM user `burn-agent-<agent_id>` (path `/burn-agents/`) | narrow policy: invoke the allowlisted Bedrock model + `s3:ListAllMyBuckets`. Rotation = new key, old key Inactive |
| `other` | rendered scenario template, e.g. a connection string | shared credentials |

### Fingerprints
`burn_contracts.fingerprint` is the only implementation. Everyone imports it.
- **exact**: `hmac_sha256_hex(key, value)`. `key` = bytes.fromhex of `burn-system/fingerprint-key`.
- **shingle**: for the secret *body* (the random part), every window of `SHINGLE_K = 8` chars of
  `body.lower()`: `hmac_sha256_hex(key, "sh:" + window)`. Lets the exit check spot fragments and
  case changes, and add up coverage across a session's recent requests.
- The minter registers, per credential, the parts that are secret:
  `api_key` -> whole key (exact) + random body (shingles); `aws_key` -> access key id and secret
  access key (exact + shingles each); `other` -> whole value (exact) + the `{random}` part
  (exact + shingles).
- Registered rows go to `burn-fingerprints`. Rows are never deleted on rotation: they are marked
  `status=rotated`, so a leak of an old key is still caught and attributed.

### Placeholders in scenario files
Scenario `files[].content` and `customers[].notes` may contain:
- `{{cred:<agent_id>:api_key}}`
- `{{cred:<agent_id>:aws_key.access_key_id}}` and `{{cred:<agent_id>:aws_key.secret_access_key}}`
- `{{cred:shared:<name>}}`

The tool Lambda resolves them **at read time** from Secrets Manager (`AWSCURRENT`). So the raw
values exist only in Secrets Manager, and after a rotation the file shows the new value.

### DynamoDB tables (all PAY_PER_REQUEST, created by the BurnData stack)
| Table | Keys | GSIs | Writers |
|---|---|---|---|
| `burn-events` | PK `stream` (= `"all"`), SK `event_id` | `by_agent` (PK `agent_id`, SK `event_id`) | gateway, proxy, burn_api, minter (provider) |
| `burn-exposures` (the ledger) | PK `agent_id`, SK `secret_ref_or_fp` | none | gateway (recorded, inherited) |
| `burn-credentials` | PK `secret_ref` | `by_owner` (PK `owner_agent_id`) | minter |
| `burn-fingerprints` | PK `fingerprint` | `by_secret` (PK `secret_ref`) | minter |
| `burn-sessions` | PK `session_id` | `by_agent` (PK `agent_id`), `by_client` (PK `client_id`) | burn_api |
| `burn-scenario` | PK `pk` (`meta`, `agent#<id>`, `ticket#<id>`, `file#<path>`, `customer#<id>`) | none | scenario loader |
| `burn-messages` | PK `to_agent`, SK `msg_id` | none | gateway tools (send_message / read_inbox) |

`burn-exposures` SK is the `secret_ref`, or `fp#<fingerprint>` for unknown-format credentials
(those have no `secret_ref`).

### Events
- Build every event with `burn_contracts.events.make_event(...)`. It fills `event_id` and `ts`
  and rejects unknown enum values, so all writers stay schema-valid.
- `detail` is for humans. Never put a credential value, a full URL with a query string, or a
  request body in it.

### Trust boundary for the exit check
- **In-boundary destinations**: Bedrock runtime (it legitimately sees tool output in the
  conversation) and the AgentCore Gateway (tool arguments are checked by the pre-call interceptor
  instead). The proxy still applies badge checks and the allowlist to them, but skips the
  body exit check.
- A credential going to its own home service in its expected header is not a leak: the agent's
  AWS key in the SigV4 `Authorization` header to `*.amazonaws.com`, and the agent's `api_key` in
  `Authorization: Bearer` to the mock provider host.
- Everything else leaving the agents' network is checked.

### Mock flag (development only)
- Components switch between mock and real by **URL only** (`BURN_API_URL`, `BURN_GATEWAY_URL`).
  No code paths are special-cased.
- Mock servers set response header `x-burn-mock: true`. The dashboard shows a red MOCK DATA
  banner whenever it sees it. The runbook checks the banner is absent before the demo.
