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
- **Canaries**: `POST /sessions/{id}/canary` gives each session its own decoy key (looks like an `api_key`, never authenticates). The provider answers it with 401, writes one `canary_used` event and hands the incident to the Burn API, which contains only that session. Interfaces for other components: [CANARIES.md](CANARIES.md).
- **Flag levels**: `POST /flags` raises a credential the agent does not own to `seen` < `attempted` < `reported`. It never lowers the level, keeps the first evidence per level, and never rotates.
- **Flag review**: one flag per credential with its highest level and `instance_count`; `GET /flags` lists them and `POST /flags/resolve` lets an owner rotate (new version, flag cleared) or dismiss, with a reason.
- **Canary origin**: the provider checks the egress proxy's `x-burn-session-proof` (`session_proof.py`). Only use from outside Burn is dispatched for a possible burn; use through a Burn session is an alert.
- `GET /agents/{id}/key-activity` returns the agent's current and previous AWS key with their IAM status (active or inactive), plus their CloudTrail events.

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
