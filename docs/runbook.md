# Runbook

How to deploy Burn into an AWS account, run the demo, and reset it between runs. Everything runs
in `us-east-1`. Every command is run from the repo root unless stated otherwise.

## Prerequisites

- AWS credentials for the target account in your shell (`aws sts get-caller-identity` works), with
  Bedrock model access to Claude Haiku 4.5 in `us-east-1`.
- Python 3.12 and [uv](https://docs.astral.sh/uv/), Node.js 20+, Docker (Docker Desktop or Colima).
- [gitleaks](https://github.com/gitleaks/gitleaks) for the pre-commit secret gate.

## First-time setup

```bash
make hooks                                        # gitleaks pre-commit gate
(cd infra && npx -y aws-cdk@2 bootstrap)          # once per account and region
make deploy                                       # all six stacks, about 10 minutes
make env                                          # writes .env and dashboard/.env.local (gitignored)
make e2e                                          # live containment check, see below
```

`make e2e` opens a session for one agent, uses its badge, burns it, and checks that the badge,
the API key and the AWS key all stop working. It prints status codes and fingerprint prefixes
only.

## Run the demo

Two terminals:

```bash
make demo            # resets demo state, mints fresh credentials, starts proxy + agents in Docker
make dashboard       # http://localhost:5173
```

`make demo-step` holds the reader so the presenter advances one ticket per Enter.
`make demo SCENARIO=scenarios/billing-ops.yaml` runs a second scenario through the same code.

Start the agents at least 15 minutes before an audience arrives: CloudTrail delivers events
several minutes late, and the blast-radius view only shows what CloudTrail has recorded.

### During the demo

| Action | Dashboard | Command line |
|---|---|---|
| Burn an agent | **Burn** on the agent's card | `make burn ARGS="kill agent-analyst --yes"` |
| Show the old key is dead | `rejected` event in the stream | `make replay-old-key AGENT=agent-analyst` |
| Exit check | Blocked handoffs (an agent passing a credential to another) appear in the stream from real traffic | Verify the outbound proxy on demand with `make exit-check-test AGENT=agent-...` |
| Blast radius | **Blast radius** panel | `make burn ARGS="blast-radius agent-analyst"` |

Other commands: `make burn ARGS="agents"`, `"events --follow"`, `"ledger agent-analyst"`. The CLI reads `BURN_API_URL` and `BURN_DEMO_TOKEN` from `.env` (written by `make env`).

The step-by-step script for a four-minute demo is in [demo-script.md](demo-script.md).

## Between runs

```bash
make reset           # clears demo state and deletes the scenario's minted credentials
make demo
```

`make demo` also resets first, so a fresh run never reuses a burned agent's credentials.

## Checks

```bash
make test            # every Python component, offline
make test-dashboard  # dashboard tests and production build
make eval            # offline catch rate and false positives, rewrites eval/RESULTS.md
make e2e             # live, against the deployed stack
```

## What it costs

A normal demo run is a handful of Cognito token requests (one per agent session, $0.00225 each),
a few hundred Claude Haiku 4.5 calls, and Lambda, DynamoDB and Gateway calls in the free-tier
range. Load tests in `eval/` create one badge and one token request per simulated agent, so their
cost grows with the agent count; check the count before running one.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Dashboard says "API unreachable" | The API accepts browser requests only from `http://localhost:5173`. Open exactly that URL (not `127.0.0.1`). `make dashboard` refuses to start if port 5173 is taken; use the existing dashboard or stop only a dev server you started. |
| Dashboard shows a MOCK DATA banner | `dashboard/.env.local` is missing or stale. Run `make env` and restart `make dashboard`. |
| An agent exits at start with "no AWS key of its own" | Its session has no minted AWS key. Run `make reset && make demo`. Agents never fall back to the host's credentials. |
| An agent's first model call fails with `InvalidClientTokenId` | A new IAM key takes a few seconds to become valid. The agent retries for about 20 seconds. |
| Blast radius is empty | CloudTrail lag. Wait a few minutes and reload. |
| `make deploy` fails on a name conflict | A previous deployment left a resource behind. `make teardown` removes everything Burn created. |

## Teardown

```bash
make teardown        # asks first; destroys every stack and the system secrets
```

## FAQ

**Which AWS resources does Burn create outside the CDK stacks?** Per-agent IAM users under the
`/burn-agents/` path and secrets under `burn/`, created by the minter at runtime. `make reset`
deletes them; `make teardown` runs the reset before destroying the stacks.

**Does any of this need real credentials of a third party?** No. The API keys belong to a mock
provider that Burn deploys, and every AWS key is a fresh IAM key inside a permissions boundary.
