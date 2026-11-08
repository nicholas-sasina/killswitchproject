# Runbook

How to deploy Burn into an AWS account, run the demo, and reset it between runs. Everything runs
in `us-east-1`. Every command is run from the repo root unless stated otherwise.

## Prerequisites

- AWS credentials for the target account in your shell (`aws sts get-caller-identity` works), with
  Bedrock model access to Claude Haiku 4.5 in `us-east-1`.
- Python 3.12 and [uv](https://docs.astral.sh/uv/), Node.js 24+, Docker (Docker Desktop or Colima).
- [gitleaks](https://github.com/gitleaks/gitleaks) for the pre-commit secret gate.

The shared-account integration owner deploys and resets the shared stack. Local tests and
the Docker emulator check below are separate from that stack. Live demos, end-to-end checks
and load tests can create paid resources or change sessions; coordinate approval before
running them against the shared account.

## First-time setup

```bash
make hooks                                        # gitleaks pre-commit gate
(cd infra && npx -y aws-cdk@2 bootstrap)          # once per account and region
make deploy                                       # deploy the six application stacks
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

`make demo-step` holds the reader and advances one ticket per Enter.
`make demo SCENARIO=scenarios/billing-ops.yaml` runs a second scenario through the same code.

Allow at least 15 minutes of agent activity before inspecting the blast-radius view:
CloudTrail delivers events several minutes late, and the view only shows recorded events.

### Operations

| Action | Dashboard | Command line |
|---|---|---|
| Burn an agent | **Burn** on the agent's card | `make burn ARGS="kill agent-analyst --yes"` |
| Verify old credentials are refused | `rejected` event in the stream | `make replay-old-key AGENT=agent-analyst` |
| Exit check | Blocked handoffs (an agent passing a credential to another) appear in the stream from real traffic | Verify the outbound proxy on demand with `make exit-check-test AGENT=agent-...` |
| Blast radius | **Blast radius** panel | `make burn ARGS="blast-radius agent-analyst"` |

Other commands: `make burn ARGS="agents"`, `"events --follow"`, `"ledger agent-analyst"`. The CLI reads `BURN_API_URL` and `BURN_DEMO_TOKEN` from `.env` (written by `make env`).

The command walkthrough and expected observations are in [demo-script.md](demo-script.md).

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
python3 detection/scripts/test_containment.py  # disposable local Docker + AWS emulator
make e2e             # live, against the deployed stack
```

Generate a draft offline report without replacing the published aggregate:

```bash
cd eval
uv run --python 3.12 python run_offline.py --trials 10 --output-dir out/current
```

`make eval` writes the tracked `eval/RESULTS.md` and `eval/results.json` instead. Review every
layer's source commit, evidence scope and unmeasured sections before publishing those artifacts.
The original deployed measurements remain under `eval/reports/initial-deployed.*`.
The Docker check proves behavior in isolated containers with emulated AWS services; it does
not prove IAM permissions, async Lambda delivery or production latency in the shared stack.

For a specific proxy configuration, use the private settings and session-projection workflow
in [the evaluation guide](../eval/README.md). Its destination audit imports into a draft report
with `--destination-evidence`; it does not contact the endpoints or certify remote permissions.
The same guide describes local handler probes, including replay and failed-rotation checks,
and read-only quota snapshots. Keep each run's evidence in its own ignored output directory.
An applied quota snapshot does not identify the cause of a throttled request.

## What it costs

Live runs consume Cognito tokens, Bedrock model calls, Gateway, Lambda, DynamoDB and other
service usage. Do not assume free-tier coverage. `eval/cost.py` lists the pricing date and
usage assumptions behind its estimates; measured usage replaces only the stated inputs.
Load tests normally create one badge per simulated agent, or share the explicitly requested
`--badges` count. Confirm those counts and the expected cost before a live run.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Dashboard says "API unreachable" | The API accepts browser requests only from `http://localhost:5173`. Open exactly that URL (not `127.0.0.1`). `make dashboard` refuses to start if port 5173 is taken; use the existing dashboard or stop only a dev server you started. |
| Dashboard shows a MOCK DATA banner | The configured service returned `x-burn-mock: true`. Point the local configuration at the intended service and restart the dashboard. |
| Dashboard asks to connect | Required local configuration is missing. The integration owner can generate it with `make env`; local mock development uses the mock service URL and token. |
| An agent exits at start with "no AWS key of its own" | Its session has no minted AWS key. Run `make reset && make demo`. Agents never fall back to the host's credentials. |
| An agent's first model call fails with `InvalidClientTokenId` | A new IAM key takes a few seconds to become valid. The agent retries for about 20 seconds. |
| Blast radius is empty | CloudTrail lag. Wait a few minutes and reload. |
| `make deploy` fails on a name conflict | Ask the integration owner to inspect the existing stack and resource ownership. `make teardown` destroys resources and is not a general repair command. |

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
