# burn_api

## What it does
The public Burn API behind the dashboard, the scenario runner and the eval harness. It starts agent
sessions (badge + credentials), burns an agent in one call, and feeds the dashboard its events. It also
runs swarms: roles, join tokens, instances that appear on their own, agents that spawn agents, and
burning an instance, a role, a lineage or a join token in one call.

## How it works
- One Lambda behind an API Gateway HTTP API; `handler.py` routes every path in `contracts/burn-api.openapi.yaml`.
- Who may call what is decided in `auth.py`, in one of two modes:
  - **Sign-in off** (the default): every request carries `x-burn-demo-token`, compared in constant time with
    `burn-system/demo-token`, except a join (its join token) and an instance's own calls (its session token,
    `Authorization: Bearer bst_...`).
  - **Sign-in on** (`BURN_PEOPLE_AUTH=1`, set by deploying with `-c burn:people_auth=on`): every route except
    `POST /instances` and an instance's own heartbeat and exit needs a signed-in person. API Gateway's JWT
    authorizer verifies the person's ID token, and the Lambda checks again that its claims are in
    `requestContext`; it never reads them from a header or the body. The demo token then works only with
    `BURN_LOCAL_DEV=1`, which a deployment never sets.
  - Either way the caller is recorded: `created_by` (the person's email) on new join tokens and sessions,
    with instances inheriting it from their token or parent, and `actor` on every event (a person, `admin`,
    or `instance:<session_id>`).
- `POST /sessions` asks the minter for the agent's credentials, creates a Cognito badge, and writes a `burn-sessions` row.
- `POST /burn` flips the agent's sessions to `revoked` (the gateway refuses revoked badges on every call), then has the minter rotate the agent's own credentials and flag shared ones from its ledger.
- Every step writes an event to `burn-events`; `GET /events?since=` is what the dashboard polls.
- **Canary incidents** arrive by direct invoke from the mock provider (`{"canary_incident": ...}`, never over HTTP). `canary.py` re-checks attribution and contains only that one session: a conditional revoke, then rotation only if no other live session uses the same credentials. See `minter/README.md` (Canaries).
- Burns flag shared credentials the agent saw as `seen`, and as `attempted` if one of its `blocked` events names them.
- Canary incidents honour the role: `auto_burn: false` (report-only) alerts and flags, never burns; `canary.issue_for_session` skips roles with `canaries: false`.
- `GET /flags` and `POST /flags/resolve` (`flag_review.py`): owners rotate or dismiss a flagged credential, with a reason.

## Swarms: roles, join tokens, instances
A **role** says what its instances may do (tools, credentials, egress, who they may spawn). An
**instance** is one session of a role with its own agent id `agent-<role>-<6 hex>`, so its
credentials, ledger and burn are its own. Scenario agents are roles with one instance
(`managed_by: scenario`), built from `burn-scenario` on every read and never stored.

| Route | What |
|---|---|
| `GET /roles`, `GET /roles/{name}` | Stored and scenario roles, with `active_instances` |
| `POST /roles` | Create a role: 201, or 409 if the name is taken (including scenario roles) |
| `PUT /roles/{name}` | Create or replace. 409 if `managed_by` differs from the stored role, or the role is a scenario role |
| `POST /roles/{name}/tokens` | Mint a join token `bjt_<token_id>.<secret>` (`uses` 1-10000, `ttl` 60-2592000 s). Returned once; only `sha256(secret)` is stored |
| `DELETE /tokens/{token_id}` | Revoke a token. Instances that already joined keep running |
| `POST /sessions {role, enroll_token}` | Join with a token. The token's role must match; 403 if invalid, expired, used up or revoked |
| `POST /sessions {role, parent_session}` | Spawn a child. The parent must be active and the role must be in the parent role's `may_spawn`. Tools, credentials and egress are the role's intersected with the parent's (a null egress takes the other side's list) |
| `GET /instances?role=&status=` | Instances, newest first (scenario sessions included). Runs the idle sweep first |
| `POST /instances/{session_id}/heartbeat` | Sets `last_seen`; 409 if the instance is not active |
| `POST /instances/{session_id}/exit` | Clean exit: status `exited`, badge revoked, the instance's own credentials deleted through the minter. Scenario agents keep their credentials |
| `POST /burns {scope, id, reason}` | Burn `instance`, `role`, `lineage` (instance plus descendants) or `token` (every instance that joined with it; the token is revoked too) |

- **Join tokens** are redeemed with one DynamoDB conditional update (`uses_left > 0`, not revoked, not
  expired), so 20 parallel joins can never use a token more than its `uses`. The secret is compared by
  hash in constant time and is never logged or put in an event. If a join fails after redeeming, the use
  is given back.
- **Session rows** keep their existing fields and add `role`, `parent_session`, `token_id`, `owner`,
  `egress`, `credentials` (the credential types minted, used to narrow grandchildren), `last_seen` and
  `exited_at`. `tools` comes from the (narrowed) role, so the Gateway enforces it unchanged. `send_to` is
  `[]` for role instances.
- **Idle instances.** There is no scheduler. `GET /instances` first exits active role instances whose
  `last_seen` is older than `BURN_INSTANCE_IDLE_SECONDS` (default 600). The sweep is bounded so the poll
  stays fast: at most 10 instances or about 2 s per call, the rest on later calls. The status flip, badge
  revoke and `instance/exited` event happen inline; deleting the instance's credentials (a slow IAM call)
  is queued on the minter with an asynchronous invoke, not awaited. If queuing fails, calling
  `POST /instances/{id}/exit` again deletes them. Scenario sessions never heartbeat and are never swept.
  Clients that keep an instance alive call heartbeat more often than the idle window.
- **Scoped burns.** Every target session is revoked first, in the pool, so they are stopped even if the
  rest is slow. A lineage is rescanned after each pass until nothing new turns up, and a child that is
  still starting re-checks its parent (or token) after its row is written, so a spawn or join cannot
  slip past a burn. Then each distinct agent goes through `POST /burn` in a pool of 10 threads. A failed
  revoke does not drop the agent (the burn revokes its sessions again), and a failed burn lands in
  `failed` without stopping the others. Agents that were already revoked are not burned again, since a
  second rotation would move `AWSPREVIOUS`. Finish a failed one with `POST /burn {agent_id}`.
- **The 29 s API limit.** A burn is a few DynamoDB calls plus one minter call (IAM key rotation). With 10
  workers, 20 instances run as two waves, so the call fits if one rotation takes under about 10 s. This is
  not measured against real AWS. To stay inside the limit regardless, the call stops waiting after 24 s
  and reports unfinished instances in `failed`.
- **Throttling.** If IAM, Cognito or the minter rate-limit a request, the API answers `429` with a
  `Retry-After` header and `retry_after_seconds`, never 500. Clients retry with backoff.
- **Lookups** by role, parent session or token are filtered scans of `burn-sessions` (`db.py`), fine
  at this scale. GSIs on those attributes are the next step.
- `POST /burn`, the ledger and blast radius also accept instance agent ids: `db.get_agent` falls back to
  the instance's session.

## Key files
| File | What |
|---|---|
| `lambdas/api/handler.py` | Auth, routing, error mapping |
| `lambdas/api/sessions.py` | Start a session, revoke sessions |
| `lambdas/api/burn.py` | The burn sequence, outcome events, flag levels |
| `lambdas/api/canary.py` | Canary incident: role policy, trusted-signal checks, single-session containment |
| `lambdas/api/flag_review.py` | Owner review of flags: list, rotate, dismiss |
| `lambdas/api/roles.py` | Roles: validation, scenario roles, create/replace rules |
| `lambdas/api/tokens.py` | Join tokens: create, atomic redeem, revoke |
| `lambdas/api/instances.py` | Instances: list, heartbeat, exit, idle sweep |
| `lambdas/api/burn_scopes.py` | `POST /burns`: resolve targets, revoke, burn in a pool |
| `lambdas/api/errors.py` | HTTP errors and throttle detection |
| `lambdas/api/reads.py` | Events feed, ledger, badge status, blast radius |
| `lambdas/api/minter_client.py` | Invokes the credential minter Lambda |
| `scripts/make_env.py` | Writes `.env` and `dashboard/.env.local` from the deployed stacks |
| `scripts/smoke.py` | Live check: session, burn, badge, blast radius |
| `scripts/e2e.py` | Live containment check: burn, gateway refuses the same token, old keys rejected |
| `scripts/canary_harness.py` | Local (moto) provider -> Burn API -> minter canary path, with timings |
| `../infra/burn_infra/burn_api.py` | The BurnApi CDK stack |

## How to test it
```bash
cd burn_api && uv run pytest -q          # unit tests (moto, fake minter)
python burn_api/scripts/smoke.py         # against the deployed API; skips if it is not deployed
cd burn_api && uv run python scripts/e2e.py   # full burn flow incl. gateway and provider (scenario must be loaded)
```

## FAQ
- **Does revoking delete the agent's access token?** No. Deleting the Cognito client only stops new
  tokens. The real kill switch is the session row: the gateway checks it on every call, so an
  already-issued token is refused immediately after a burn.
- **What if the agent never saw any credentials, or has no session?** Burn still works: nothing is
  revoked or rotated beyond what exists, and the response lists exactly what happened.
- **Why one Lambda for all routes?** The API is small; one router file is easier to read and one IAM role
  is easy to audit.
- **Is the blast radius instant?** No. CloudTrail event history can lag 5-15 minutes; the response says so.
