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
- `GET /agents/{id}/key-activity` reads CloudTrail for the agent's current and previous access key ids.

## Key files
| File | What |
|---|---|
| `lambdas/minter/minter_app.py` | Lambda entry point and routing |
| `lambdas/minter/credentials.py` | Issue / rotate / delete for one agent |
| `lambdas/minter/api_key.py`, `aws_key.py`, `shared.py` | One credential type each |
| `lambdas/minter/fingerprints.py`, `registry.py`, `vault.py` | Fingerprint rows, `burn-credentials` rows, Secrets Manager |
| `lambdas/minter/key_activity.py` | CloudTrail lookup for the blast-radius view |
| `lambdas/provider/provider_app.py` | Mock API provider |
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
- **Does the blast-radius view show model calls?** No. CloudTrail `LookupEvents` returns
  management events only (IAM, STS, S3 bucket operations), and they can lag by several minutes.
