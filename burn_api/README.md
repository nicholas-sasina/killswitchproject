# burn_api

## What it does
The public Burn API behind the dashboard, the scenario runner and the eval harness. It starts agent
sessions (badge + credentials), burns an agent in one call, and feeds the dashboard its events.

## How it works
- One Lambda behind an API Gateway HTTP API; `handler.py` routes every path in `contracts/burn-api.openapi.yaml`.
- Every request must carry a Cognito **human access token** (`Authorization: Bearer ...`). API Gateway validates issuer, audience, expiration and the `openid` scope before invoking Lambda. The Lambda trusts only `requestContext.authorizer.jwt.claims`, never client identity headers.
- `POST /sessions` asks the minter for the agent's credentials, creates a Cognito agent badge, and writes a `burn-sessions` row containing the validated human `owner_sub` and `owner_name`. The owner is shown in the agent state feed and dashboard.
- `POST /burn` flips the agent's sessions to `revoked` (the gateway refuses revoked badges on every call), then has the minter rotate the agent's own credentials and flag shared ones from its ledger.
- Every step writes an event to `burn-events`; `GET /events?since=` is what the dashboard polls.

## Key files
| File | What |
|---|---|
| `lambdas/api/handler.py` | Auth, routing, error mapping |
| `lambdas/api/sessions.py` | Start a session, revoke sessions |
| `lambdas/api/burn.py` | The burn sequence |
| `lambdas/api/reads.py` | Events feed, ledger, badge status, blast radius |
| `lambdas/api/minter_client.py` | Invokes the credential minter Lambda |
| `scripts/make_env.py` | Writes `.env` and `dashboard/.env.local` from the deployed stacks |
| `scripts/smoke.py` | Live check: session, burn, badge, blast radius |
| `scripts/e2e.py` | Live containment check: burn, gateway refuses the same token, old keys rejected |
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

## Human sign-in setup
Deploy `BurnApi` to create a separate `burn-people` Cognito pool, public Hosted UI client and JWT authorizer. The existing `burn-badges` pool remains for **machine** credentials. No self sign-up is enabled. A privileged administrator creates and invites a user using the pool ID at SSM `/burn/auth/pool-id` (for example, `aws cognito-idp admin-create-user --user-pool-id <pool-id> --username person@example.com --user-attributes Name=email,Value=person@example.com Name=email_verified,Value=true`).

The public configuration values are in SSM `/burn/api/url`, `/burn/auth/domain`, `/burn/auth/client-id`. Run `python burn_api/scripts/make_env.py` to fill dashboard `.env.local` with `VITE_BURN_API_URL`, `VITE_COGNITO_DOMAIN`, and `VITE_COGNITO_CLIENT_ID`, then start the dashboard at `http://localhost:5173/`. Click **Sign in** to use the Cognito Hosted UI with authorization code + PKCE. Tokens are held in browser session storage and removed on sign-out. The UI disables access when the access token expires; sign in again to continue. Never place a client secret in Vite configuration.

**Integration note:** the existing CLI scenario/smoke/e2e scripts still use the old demo-token scheme and must be migrated to supply valid human access tokens before live runs. This sign-in-only change does not alter those workflows, deploy the stack, or assert that the live demo is passing. Only the localhost callback URL is configured; production dashboard URLs need explicit Cognito callback, logout and API CORS updates.
