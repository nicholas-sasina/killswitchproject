# minter

## What it does
Gives every agent its own credentials (an API key for the mock provider and a real AWS access key)
and rotates exactly one agent's credentials on burn. It also hosts the mock API provider that
accepts only the current version of each key.

## How it works
- `burn-minter` Lambda (direct invoke, routes in `contracts/minter.openapi.yaml`) writes values to Secrets Manager and only HMAC fingerprints to `burn-fingerprints`.
- Rotation puts a new secret version (the old one stays at `AWSPREVIOUS`), marks the old fingerprints `rotated`, and for AWS keys sets the old access key Inactive instead of deleting it.
- Credentials an agent merely saw (`exposed_refs` not owned by it) are marked `flagged` in `burn-credentials`.
- `burn-provider` (API Gateway HTTP API, URL in SSM `/burn/provider/url`) looks the bearer key up by exact fingerprint: current = 200, rotated = 401 plus a `rejected` event.
- **Canaries**: `POST /sessions/{id}/canary` gives each session its own decoy key (looks like an `api_key`, never authenticates). The provider answers it with 401, writes one `canary_used` event and hands the incident to the Burn API, which contains only that session. See [Canaries](#canaries).
- **Flag levels**: `POST /flags` raises a credential the agent does not own to `seen` < `attempted` < `reported`. It never lowers the level, keeps the first evidence per level, and never rotates.
- **Flag review**: one flag per credential with its highest level and `instance_count`; `GET /flags` lists them and `POST /flags/resolve` lets an owner rotate (new version, flag cleared) or dismiss, with a reason.
- **Canary origin**: the provider checks the egress proxy's `x-burn-session-proof` (`session_proof.py`). Only use from outside Burn is dispatched for a possible burn; use through a Burn session is an alert.
- `GET /agents/{id}/key-activity` returns the agent's current and previous AWS key with their IAM status (active or inactive), plus their CloudTrail events.

## Canaries

### The path

```
session start ──POST /sessions/{sid}/canary──▶ minter: value -> Secrets Manager, HMACs -> burn-fingerprints
agent reads a doc with {{canary}} ──Gateway──▶ ledger row (agent_id, canary_ref)      = exposure, no alert
canary leaks (any encoding) ─▶ attacker decodes ─▶ GET /v1/whoami  Bearer <canary>
provider: exact fingerprint has canary=true ─▶ 401 "invalid key" (same as unknown)
          origin from x-burn-session-proof: through a Burn session ─▶ canary_used ALERT, stop
          from outside Burn ─▶ canary_used + async invoke burn-api {"canary_incident": {secret_ref, event_id}}
burn-api: re-verify attribution ─▶ role auto_burn off? ─▶ ALERT + flag, stop (report-only)
          ─▶ conditional revoke of THAT session ─▶ delete badge
          ─▶ rotate the agent's own credentials (only if no other active session uses them)
          ─▶ flag shared credentials it saw (seen; attempted if a check blocked them)
```

Detection does not depend on how the canary escaped. It works because whoever uses the canary has
to present it, in plain form, to a service Burn monitors. It does not see inside encrypted
traffic.

### Burn or alert

| Canary used... | Result |
|---|---|
| from outside Burn (no valid session proof), role `auto_burn` on (default) | burn that one instance, if the trusted-signal policy below holds |
| from outside Burn, role `auto_burn: false` (report-only) | `canary_used` alert + flags on what it saw; never a burn |
| through the instance's own session | alert only. Enforcing proxy: blocked before it leaves (`blocked` event naming the canary). Forwarding proxy: `canary_used` "own session; alert only" |
| through another Burn session | `canary_used` alert for review (the canary reached a second instance) |

If a role-based session's role record cannot be read, nothing is burned.

### Where to plant canaries

Only in text the Gateway fills, scenario `files[].content` (`read_config`) and `customers[].notes`
(`query_customers`); tickets and messages are returned as written, so a `{{canary}}` there is never filled
([contracts/README.md](../contracts/README.md), Placeholders). Within those, only where an honest agent has
no reason to use it, so any use means something went wrong:
- a commented-out line in a backup or `.bak` config: `# PROVIDER_API_KEY={{canary}}  # pre-rotation, dead`
- a revoked key in an archived migration note kept as a file: `old provider key (revoked 2024-03, do not use): {{canary}}`
- a revoked key in an old customer note: `support pasted their old key here (revoked): {{canary}}`

Never in the config, runbook or ticket the agent needs for its task. It already has its own `api_key` for
the provider, so using the canary is never the honest path.

### Trusted-signal policy (what may contain automatically)

All of these must hold, or the incident is recorded (`flagged` event, "not contained: <reason>") and
nothing is revoked:

1. The ref is a registered canary bound to exactly one agent + session (re-read from `burn-credentials`).
2. That agent's ledger has the canary, and no other agent's does (a canary inherited through
   `send_message` is ambiguous).
3. The session (the instance's identity) exists, belongs to that agent, and is `active`.

The worst a deliberately replayed canary can do is contain the one session it was planted in. That
session is the only one that ever read it. Repeated presentations only increment a counter, so
they cannot cause repeated rotations or flood the event feed.

### Measured locally
`burn_api/scripts/canary_harness.py` runs the real provider, Burn API and minter handlers in-process
on moto; session start, `{{canary}}` resolution and the leak are simulated:

```bash
cd burn_api && uv run python scripts/canary_harness.py --workers 20 --runs 5
```

With 20 workers on a laptop (moto, in-memory): incident received -> session revoked 7.3–8.1 ms;
-> rotation of api_key + aws_key done 90.3–113.6 ms (5 runs). Real DynamoDB, IAM and Lambda async-invoke latency
are not measured here.

## Key files
| File | What |
|---|---|
| `lambdas/minter/minter_app.py` | Lambda entry point and routing |
| `lambdas/minter/credentials.py` | Issue / rotate / delete for one agent |
| `lambdas/minter/api_key.py`, `aws_key.py`, `shared.py` | One credential type each |
| `lambdas/minter/fingerprints.py`, `registry.py`, `vault.py` | Fingerprint rows, `burn-credentials` rows, Secrets Manager |
| `lambdas/minter/key_activity.py` | CloudTrail lookup for the blast-radius view |
| `lambdas/minter/canaries.py` | Per-session canaries: issue (idempotent), read metadata, reset |
| `lambdas/minter/flags.py` | Monotonic flag levels with bounded evidence |
| `lambdas/provider/provider_app.py` | Mock API provider |
| `lambdas/provider/canary_trip.py`, `shared_report.py` | Canary presentations; flagged shared keys seen in use |
| `lambdas/provider/session_proof.py` | Verifies the egress proxy's `x-burn-session-proof` |
| `scripts/smoke.py` | Live smoke test with a throwaway agent |
| `scripts/replay_old_key.py` | Replays the pre-rotation keys and shows they are rejected |
| `../infra/burn_infra/minter.py` | The `BurnMinter` stack (roles, boundary policy, API) |

## How to test it
```bash
cd minter && uv sync
uv run pytest -q                                  # unit tests on moto, no AWS needed
uv run python scripts/smoke.py                    # live: issue, call, rotate, replay, clean up
uv run python scripts/replay_old_key.py --agent agent-reader   # after that agent was burned
```
Deploy: `cd infra && npx aws-cdk@2 deploy BurnMinter --require-approval never`.

## FAQ
- **Can the minter escalate privileges through IAM?** No. Its role may only create users (or put
  their policy) when the request sets the `burn-agent-boundary` permissions boundary, only for
  users under `/burn-agents/`, and it is explicitly denied from changing or removing a boundary.
  Even a bug in the minter cannot create a user that can do more than invoke the allowlisted
  Bedrock model and `s3:ListAllMyBuckets`. The one `"*"` resource is `cloudtrail:LookupEvents`,
  which AWS does not support at resource level.
- **Why does a new AWS key sometimes fail for 5 to 15 seconds?** IAM is eventually consistent.
  The Lambda does not sleep; whoever uses a fresh key (the agent runner) should retry briefly.
- **Why is the old AWS key kept instead of deleted?** So AWS visibly rejects it
  (`InvalidClientTokenId`) and its CloudTrail history stays easy to find. It is deleted on the
  next rotation (IAM allows two keys per user).
- **Can a canary ever authenticate?** No. The provider checks the `canary` marker first and always
  answers 401. The fingerprint rows also say `secret_type=other`, so even a provider build that ignores
  the marker refuses it. Rotation skips canaries, so no real key is ever minted under a canary ref.
- **Does the blast-radius view show model calls?** No. CloudTrail `LookupEvents` returns
  management events only (IAM, STS, S3 bucket operations), and they can lag by several minutes.
