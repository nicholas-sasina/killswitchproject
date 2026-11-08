# contracts

## What it does
The source of truth every other folder codes against: event format, API shapes, MCP tool list,
scenario format, and the shared naming/fingerprint rules below.
A contract change updates this folder and the mocks together.

## How it works
- JSON Schema for data (`ledger-event.schema.json`, `scenario.schema.json`, `burn.schema.json`),
  OpenAPI for APIs.
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
| `burn.schema.json` | `burn.yaml` format (roles defined in code); example in `examples/burn.yaml` |
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
- Session ids are opaque strings issued by `POST /sessions`. A session is one **instance**; the
  session id is the instance's identity.
- Role names: `^[a-z0-9][a-z0-9-]{0,31}$`. Each instance of a role gets its own agent id
  `agent-<role>-<6 hex>` (`names.instance_agent_id`), so its credentials, ledger and burn are its own.
  A scenario agent is a role with one instance: `agent-reader` is role `reader`
  (`names.scenario_role_name`), managed_by `scenario`.
- Region: `us-east-1` only. All resource names are fixed (below) so stacks reference each other by
  name instead of CloudFormation exports. See `python/burn_contracts/names.py`.

### Secrets Manager layout
| Secret name | Value | Written by |
|---|---|---|
| `burn/<agent_id>/api_key` | raw mock-provider key | minter |
| `burn/<agent_id>/aws_key` | JSON `{"access_key_id","secret_access_key","iam_user"}` | minter |
| `burn/<agent_id>/badge` | JSON `{"client_id","client_secret","token_url","scope"}` | burn_api (sessions) |
| `burn/shared/<name>` | raw value rendered from the scenario template | minter |
| `burn/<agent_id>/canary_<h>` | raw canary value; `<h>` = first 24 hex of sha256(session_id) (`names.canary_secret_ref`) | minter |
| `burn-system/fingerprint-key` | 64 hex chars, HMAC key for fingerprints | infra (BurnData) |
| `burn-system/demo-token` | token for the `x-burn-demo-token` header | infra (BurnData) |

Rotation keeps the previous value at version stage `AWSPREVIOUS`. That is how the demo
replays the pre-rotation key without anyone having stored a copy.

### Credential formats
| type | Format | Notes |
|---|---|---|
| `api_key` | `mpk_live_` + 32 chars `[A-Za-z0-9]` (`secrets.choice`) | accepted by the mock API provider only while current |
| join token | `bjt_` + 16 hex token id + `.` + 43 chars `[A-Za-z0-9_-]` (`secrets.token_urlsafe(32)`) | shown once at creation; only sha256 of the secret part is stored. `names.parse_join_token` |
| `aws_key` | a real IAM access key on IAM user `burn-agent-<agent_id>` (path `/burn-agents/`) | narrow policy: invoke the allowlisted Bedrock model + `s3:ListAllMyBuckets`. Rotation = new key, old key Inactive |
| `other` | rendered scenario template, e.g. a connection string | shared credentials |
| canary | `mpk_live_` + 32 chars `[A-Za-z0-9]`, one per session; looks like an `api_key` | **never authenticates**. Registry rows have `secret_type=canary`; fingerprint rows have `secret_type=other` plus `canary=true` and `session_id` (so detectors that predate canaries still treat it as a normal registered credential, and no provider build can ever accept it) |

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
- `{{canary}}`: the **calling session's** canary. It takes no argument: the Gateway resolves it from the
  verified badge session (`names.canary_secret_ref(session.agent_id, session.session_id)`), so one session
  can never read another session's canary. Reading it is recorded in the ledger like any credential; only
  presenting it to the provider is an incident.
  **Placement**: put `{{canary}}` only where an honest agent has no reason to use it, e.g. a revoked key in
  an archived migration note, a commented-out line in a backup file, another team's old ticket. Never in
  the config or instructions the agent needs for its task (it already has its own `api_key` for that).

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
| `burn-roles` | PK `name` | none | burn_api (roles) |
| `burn-enroll-tokens` | PK `token_id` | `by_role` (PK `role`) | burn_api (tokens) |

`burn-exposures` SK is the `secret_ref`, or `fp#<fingerprint>` for unknown-format credentials
(those have no `secret_ref`).

`burn-sessions` rows keep their existing fields (`session_id`, `agent_id`, `client_id`, `status`,
`tools`, `send_to`, `created_at`). Instances add optional fields, never renaming or removing one:
`role`, `parent_session`, `token_id`, `owner`, `egress`, `credentials` (the narrowed credential types
minted for the instance), `last_seen`, `exited_at`.
`status` is `active`, `revoked` (burned) or `exited` (clean exit or inactivity).

`burn-enroll-tokens` rows: `token_id`, `role`, `secret_sha256`, `uses`, `uses_left`, `expires_at`,
`revoked`, `created_at`. The token itself is never stored.

### Roles and instances
- A role (`burn-roles`) has `tools`, `credentials`, `may_message` (roles), `may_spawn` (roles),
  `egress` (hosts, or null for the default allowlist), `owner` and `managed_by`: `code` (burn.yaml via
  `burn apply`, read-only in the dashboard), `ui` (made in the dashboard) or `scenario`.
- An instance starts with a join token (`{role, enroll_token}`) or from a running parent
  (`{role, parent_session}`). A child's role must be in the parent role's `may_spawn`. Its tools and
  credentials are the role's intersected with the parent's; its egress is the role's list
  intersected with the parent's (null on one side means take the other side's list).
- The session row's `tools` comes from the role, so the Gateway enforces it as before.
- Burn scopes (`POST /burns`): `instance`, `role`, `lineage` (an instance and every descendant),
  `token` (every instance that joined with it; the token is revoked too).

### Canaries and flag levels
- **Canary incident**: the mock provider recognises a canary by its exact fingerprint, always answers 401
  (same body as an unknown key), writes one `canary_used` event (`source=provider`) and hands the incident to
  the Burn API. Repeated presentations only bump a counter on the canary's registry row.
- **Where it was used from** decides burn vs alert:
  - *outside Burn* (no valid session proof): the only case that may burn, and only if the role has
    `auto_burn` on (default). `auto_burn: false` makes the role report-only: alert and flag, never burn.
  - *through the instance's own session*: an alert, never a burn. With the proxy enforcing, the exit check
    blocks the canary before it leaves (a `blocked` event naming the canary). If the proxy ever forwards it
    (report-only egress), it adds `x-burn-session-proof` and the provider records `canary_used` as an alert.
  - *through another Burn session*: an alert plus a review flag (the canary reached a second instance).
- **Session proof header** (set by the egress proxy on requests to the provider; the proxy drops any copy
  the agent sent): `x-burn-session-proof: v1.<session_id>.<unix_ts>.<hmac>` where
  `hmac = hmac_sha256_hex(fingerprint_key, "burn-session-proof:v1:<session_id>:<unix_ts>")`. The provider
  accepts it for 120 s. Agents never hold the fingerprint key, so they cannot forge one.
- **Trusted-signal policy** (what may burn, on top of the above): the canary maps to exactly one session;
  that session's agent has the canary in its exposure ledger and no other agent does; the session row
  still exists, belongs to the same agent, and is `active`. Containment is the conditional flip of that one
  session `active -> revoked`, so concurrent or repeated incidents contain once. Anything else is recorded
  and flagged for review, never contained, and never widened to the agent or role.
- **Roles**: `canaries` (default true) gives each instance a canary; `auto_burn` (default true) as above.
- **Flag levels** for credentials an agent saw but does not own (`burn-credentials.flag_level`):
  `seen` (entered an agent's context) < `attempted` (an exit or tool-argument check blocked it leaving)
  < `reported` (trusted evidence it was presented at a monitored service). Levels only go up; each level keeps
  the first evidence that reached it (`flag_evidence_<level>`, safe metadata only). `status` stays `flagged`
  for older readers. Flagged credentials are never rotated automatically.
- **One flag per credential**, on its `burn-credentials` row: highest level, `instance_count` (distinct
  agent ids whose evidence flagged it; at most 500 ids kept), first evidence per level. Owners resolve it
  with a reason (`POST /flags/resolve`): `rotate` mints a new version (agents get it through placeholders)
  and clears the flag; `dismiss` keeps the value and closes the flag. A dismissed flag reopens only on
  evidence at a higher level than the level it was dismissed at.

### Events
- Build every event with `burn_contracts.events.make_event(...)`. It fills `event_id` and `ts`
  and rejects unknown enum values, so all writers stay schema-valid.
- Instance lifecycle: source `instance`, actions `started` / `exited`, with `session_id`, `role`
  and `parent_session` set. A call carrying a badge Burn never issued is `agent_id: system`,
  source `gateway`, action `unregistered`.
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
