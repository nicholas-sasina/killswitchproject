# Canary credentials and flag levels: integration interfaces

What the canary workstream provides, and the exact hooks other components call.
Contract source of truth: `contracts/README.md` ("Canaries and flag levels") and
`contracts/minter.openapi.yaml`.

## The path

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

## Burn or alert

| Canary used... | Result |
|---|---|
| from outside Burn (no valid session proof), role `auto_burn` on (default) | burn that one instance, if the trusted-signal policy below holds |
| from outside Burn, role `auto_burn: false` (report-only) | `canary_used` alert + flags on what it saw; never a burn |
| through the instance's own session | alert only. Enforcing proxy: blocked before it leaves (`blocked` event naming the canary). Forwarding proxy: `canary_used` "own session; alert only" |
| through another Burn session | `canary_used` alert for review (the canary reached a second instance) |

If a role-based session's role record cannot be read, nothing is burned.

## Where to plant canaries

Only where an honest agent has no reason to use them, so any use means something went wrong:
- a revoked key in an archived migration note: `old provider key (revoked 2024-03, do not use): {{canary}}`
- a commented-out line in a backup or `.bak` config: `# PROVIDER_API_KEY={{canary}}  # pre-rotation, dead`
- another team's closed ticket the agent may read for context

Never in the config, runbook or ticket the agent needs for its task. It already has its own `api_key` for
the provider, so using the canary is never the honest path.

## Trusted-signal policy (what may contain automatically)

All of these must hold, or the incident is recorded (`flagged` event, "not contained: <reason>") and
nothing is revoked:

1. The ref is a registered canary bound to exactly one agent + session (re-read from `burn-credentials`).
2. That agent's ledger has the canary, and no other agent's does (a canary inherited through
   `send_message` is ambiguous).
3. The session (since #38, the instance's identity) exists, belongs to that agent, and is `active`.

The worst a deliberately replayed canary can do is contain the one session it was planted in. That
session is the only one that ever read it. Repeated presentations only increment a counter, so
they cannot cause repeated rotations or flood the event feed.

## Session / instance owner (onboarding, swarms, instances)

- **Issue the canary** right after the session id exists (before the session row is written, same as
  credentials), for every path into `POST /sessions` (`agent_id`, `role` + `enroll_token`,
  `role` + `parent_session`). Use the helper, which honours the role's `canaries` flag; it is idempotent:
  ```python
  import canary
  canary.issue_for_session(agent_id, session_id, role)  # None when the role has canaries: false
  ```
  `agent_id` is the instance's own id (`names.instance_agent_id(role, suffix)`); `role` is None for
  scenario agents. Response: `{canary_ref, agent_id, session_id, version, status, issued_at}`, never the value.
- **Roles**: store and return `canaries` and `auto_burn` (both default true) in `roles.py`, like the other
  role fields (contract in #40; the mock already carries them). The canary path reads them from
  `burn-roles` by the session row's `role`.
- **Containment primitive used**: a conditional `UpdateItem` on `burn-sessions`
  (`status: active -> revoked`, `revoked_by = "canary:<event_id>"`). If sessions/instances move to
  another table, please expose `revoke_session_if_active(session_id) -> bool` with the same
  guarantee (exactly one caller gets `True`).
- **Per-instance credentials**: since #38 every instance has its own agent id, so rotating that agent's
  credentials touches only the compromised instance. As a guard, if two live sessions ever share one
  agent id, the canary path revokes only the leaking session and **defers rotation**
  (`contained_rotation_deferred`) rather than break the other session.
- **Reset**: call `DELETE /agents/{agent_id}/canaries` from the reset script. Burns and session exits
  keep canaries so they stay attributable.

## Gateway owner

1. Interceptor: inject the verified session id next to the caller id, e.g.
   `arguments["burn_caller_session_id"] = session["session_id"]`, overwriting anything the agent sent.
2. `placeholders.resolve`: support `{{canary}}` (no arguments). Resolve it to
   `names.canary_secret_ref(caller_agent_id, caller_session_id)` at `AWSCURRENT`, or
   `(credential not issued)`. Never accept an agent/session inside the placeholder, so one session
   cannot read another's canary. `{{cred:<agent>:canary_...}}` is already refused by
   `AGENT_SECRET_NAMES`. Keep it that way.
3. Nothing else changes. Canary fingerprint rows are ordinary registered rows (`secret_type=other`),
   so the response interceptor already writes the ledger row and the `recorded` event. That ledger row
   is the exposure evidence containment requires.
4. Planting: follow "Where to plant canaries" above. Never in a file the agent needs for its task.

## Egress proxy owner: session proof

On every request the proxy forwards to the provider host, drop any `x-burn-session-proof` the agent sent
and add the proxy's own:

```python
ts = str(int(time.time()))
mac = hmac_sha256_hex(fingerprint_key, f"burn-session-proof:v1:{session_id}:{ts}")
flow.request.headers["x-burn-session-proof"] = f"v1.{session_id}.{ts}.{mac}"
```

`minter/lambdas/provider/session_proof.py` has `make()` as the reference implementation. Today the exit
check blocks a canary going out, so this only matters if the proxy ever forwards one (report-only
egress). Without the header, every canary use at the provider counts as outside Burn.

## Secret-reference owner

Canaries are deliberate decoys: resolve `{{canary}}` only for the calling session, and never redact
them. Real secrets keep every existing protection. Canary values live only in Secrets Manager.

## Gateway / egress proxy owners: `attempted`

- **Real time (preferred)**: after writing a `blocked` event for a credential the agent does not own,
  invoke the minter:
  ```json
  {"method": "POST", "path": "/flags", "body": {"secret_refs": ["burn/shared/crm_db_url"], "level": "attempted",
   "evidence": {"source": "egress", "event_id": "evt_...", "agent_id": "agent-x", "session_id": "...", "tool": "notify", "ts": "..."}}}
  ```
  `source` must be `egress`, `gateway` or `handoff`. This needs `lambda:InvokeFunction` on `burn-minter`.
- **Already works at burn time**: the Burn API raises shared credentials to `attempted` from the
  agent's `blocked` events when it burns the agent (manual or canary). It needs Query on the
  `burn-events/index/by_agent` index (see infra). Without that permission it logs and skips.

## Infra owner (IAM only, no new tables)

| Role | Add |
|---|---|
| `ProviderRole` (`burn-provider`) | `dynamodb:GetItem`, `dynamodb:UpdateItem` on `burn-credentials`; `lambda:InvokeFunction` on `burn-api` and `burn-minter` |
| BurnApi role | `dynamodb:Query` on `burn-events/index/by_agent`; `dynamodb:GetItem` on `burn-roles` (role policy; #43 already grants it) |
| `MinterRole` | `dynamodb:Scan` on `burn-credentials` (`GET /flags`); canaries need nothing new (`burn/*` secrets, existing tables) |

Optional env on the provider: `BURN_API_FUNCTION`, `MINTER_FUNCTION` (defaults `burn-api`, `burn-minter`).

## Dashboard owner

- `canary_used` is in `EventAction` and the EventFeed map (added in #40). Fields (`source=provider`):
  `agent_id`, `session_id`, `secret_ref` (the canary),
  `fingerprint`, and the caller IP in `detail`.
- Follow-ups from `source=burn`, all with the same `session_id`: `revoked` (the detail
  names the incident event id and where the canary was read), `rotated`, and `flagged` with `flag_level`.
- `flagged` with no `secret_ref` and a detail starting `rotation deferred` / `rotation failed` /
  `canary incident not contained` means containment is incomplete. Show it as such.
- Ledger entries now carry `flag_level` (`seen` / `attempted` / `reported`, or null).
- `GET /flags`: one flag per credential, with `flag_level` (highest reached), `instance_count`, `state`,
  first evidence per level and the last `resolution`. `POST /flags/resolve`
  `{secret_ref, action: rotate|dismiss, reason, by}` writes a `rotated` or `dismissed` event (the
  `dismissed` label is in the EventFeed since #40).
- A role with `auto_burn: false` is report-only: canary incidents on it end as `report_only`.

## Local harness and measured timings

`burn_api/scripts/canary_harness.py` runs the real provider, Burn API and minter handlers in-process
on moto. Session start, `{{canary}}` resolution and the leak are simulated:

```bash
cd burn_api && uv run python scripts/canary_harness.py --workers 20 --runs 5
```

Measured on a laptop with moto (in-memory), 20 workers, 5 runs: incident received -> session revoked
22.6–24.0 ms; -> rotation of api_key + aws_key done 104.9–108.8 ms. These are local numbers. Real
DynamoDB, IAM and Lambda async-invoke latency are not measured here.
