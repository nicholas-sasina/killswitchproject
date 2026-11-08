# burn_api

## What it does
The public Burn API behind the dashboard, the scenario runner and the eval harness. It starts agent
sessions (badge + credentials), burns an agent in one call, and feeds the dashboard its events.

## How it works
- One Lambda behind an API Gateway HTTP API; `handler.py` routes every path in `contracts/burn-api.openapi.yaml`.
- Every request must carry `x-burn-demo-token`; it is compared in constant time with `burn-system/demo-token`.
- `POST /sessions` asks the minter for the agent's credentials, creates a Cognito badge, and writes a `burn-sessions` row.
- `POST /burn` flips the agent's sessions to `revoked` (the gateway refuses revoked badges on every call), then has the minter rotate the agent's own credentials and flag shared ones from its ledger.
- Every step writes an event to `burn-events`; `GET /events?since=` is what the dashboard polls.
- **Canary incidents** arrive by direct invoke from the mock provider (`{"canary_incident": ...}`, never over HTTP). `canary.py` re-checks attribution and contains only that one session: a conditional revoke, then rotation only if no other live session uses the same credentials. See `minter/CANARIES.md`.
- Burns flag shared credentials the agent saw as `seen`, and as `attempted` if one of its `blocked` events names them.
- Canary incidents honour the role: `auto_burn: false` (report-only) alerts and flags, never burns; `canary.issue_for_session` skips roles with `canaries: false`.
- `GET /flags` and `POST /flags/resolve` (`flag_review.py`): owners rotate or dismiss a flagged credential, with a reason.

## Key files
| File | What |
|---|---|
| `lambdas/api/handler.py` | Auth, routing, error mapping |
| `lambdas/api/sessions.py` | Start a session, revoke sessions |
| `lambdas/api/burn.py` | The burn sequence, outcome events, flag levels |
| `lambdas/api/canary.py` | Canary incident: role policy, trusted-signal checks, single-session containment |
| `lambdas/api/flag_review.py` | Owner review of flags: list, rotate, dismiss |
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
