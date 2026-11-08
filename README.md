# Burn

**Revoking an agent isn't revoking what it saw.**

Burn contains a compromised AI agent in one click: it revokes the agent's badge and kills every
credential the agent held or saw, without touching any other agent or service.

1. **Per-agent credentials.** Every agent gets its own copy of each credential it needs, so burning
   one agent never breaks another, and a leaked key traces to exactly one agent.
2. **Secret references.** Every tool response passes through the Gateway. By default, a credential Burn
   minted is swapped for a `{{burn:ref:...}}` reference before the agent sees it, so its value never
   enters the agent's context. What each agent saw is recorded in its exposure ledger (fingerprints
   only, never raw values).
3. **Canaries.** Each session gets its own canary, a decoy key that can be planted in files its tools
   read. Whoever presents it to the provider from outside Burn trips it, and by default Burn burns that
   one session.
4. **Exit check.** Messages an agent sends through Burn's tools, and its traffic through the egress
   proxy, are checked for known credentials, including re-encoded copies and copies split across
   requests.
5. **Roles.** A role sets an agent's tools, credentials and egress. A Limited role sees only the tools
   it lists, and the Gateway refuses any other.
6. **Burn.** Revoke the badge, rotate exactly that agent's credentials, flag shared credentials it
   saw, and pull CloudTrail history for its AWS keys (blast radius). With replace on burn, a clean
   replacement picks up the burned worker's task.
7. **Alerts.** High-severity incidents, such as a canary used or a credential blocked on its way out,
   go to Slack, Discord, Telegram, Microsoft Teams or email.

## Quick start

```bash
make hooks && make deploy                        # once per AWS account (us-east-1)
make deploy-BurnApi PEOPLE_AUTH=on               # switch on sign-in
make dashboard-deploy && make dashboard-hosted   # host the console on CloudFront, behind sign-in
make env                                         # write .env from the deployed stacks
make person EMAIL=you@example.com                # invite yourself; Cognito emails a temporary password
make demo                                        # egress proxy + three agents in Docker
```

`make dashboard-deploy` prints the console's address. [infra/README.md](infra/README.md) explains the
hosting. For development, `make dashboard` serves the same console on http://localhost:5173.

### Run your own agent under Burn

```bash
uv tool install "git+https://github.com/nicholas-sasina/killswitchproject#subdirectory=cli"
burn login
burn run claude   # Claude Code with its own identity, its own AWS key and Burn's tools
```

`burn run` wraps any harness that speaks MCP over HTTP (`burn run opencode`, `burn run python agent.py`)
and stops it when it is burned. [cli/README.md](cli/README.md) covers the settings it reads and how
roles work.

### A fleet and an attack

```bash
make demo SCENARIO=scenarios/support-desk-attack.yaml   # the support desk plus a poisoned ticket and a canary
make swarm N=12 ITEMS=6                                 # 12 AI workers on Claude Haiku (Bedrock: real cost, capped)
pbpaste | make attack                                   # an outsider tries whatever leaked
```

The walkthrough, with what each step should show, is in [docs/demo-script.md](docs/demo-script.md).
The full runbook (prerequisites, reset, checks, cost, teardown) is in [docs/runbook.md](docs/runbook.md);
how it fits together is in [docs/architecture.md](docs/architecture.md).

## Measured

From [eval/RESULTS.md](eval/RESULTS.md), which states each run's method and limits:

- **References:** credentials in agent context, 0/200 (Gateway handlers against moto, not a deployed stack).
- **Canaries:** uses detected 20/20, revocations recorded 20/20 (provider, Burn API and minter handlers
  against moto).
- **Exit check** (offline scanner): 76.0% of disguised credentials named on the established corpus, with
  0/310 false positives; 31.2% on a held-out corpus.

## Repo layout

| Folder | What lives there |
|---|---|
| `contracts/` | JSON Schema + OpenAPI + MCP tool definitions. Source of truth for every component. |
| `mocks/` | Mock Burn API and mock MCP tool server for development. |
| `infra/` | AWS CDK app. One stack per component. |
| `gateway/` | AgentCore Gateway, request/response interceptor Lambdas, tool Lambdas over scenario data. |
| `minter/` | Credential minter, rotation, canaries, mock API provider, CloudTrail query. |
| `detection/` | Shared detector library and the egress proxy (mitmproxy addon). |
| `burn_api/` | Burn API Lambdas: sessions, roles, burn, blast radius, events. |
| `agents/` | Strands agents, scenarios, the AI swarm and its launcher. |
| `cli/` | `burn` command-line client and `burn run`. |
| `dashboard/` | React + Vite console, run locally or hosted. |
| `notifications/` | Incident alerts to Slack, Discord, Telegram, Microsoft Teams and email. |
| `site/` | The project website. |
| `eval/` | Catch rate, false positives, latency, load test, cost. |
| `docs/` | Architecture, runbook, demo walkthrough. |
| `scripts/` | Repository tooling: the pre-commit secret gate. |

## Secrets policy (this repo is public)

- Burn mints every credential it uses at runtime, with random values. Nothing real is checked in.
- `.env`, `.run/`, certs, CDK outputs and raw eval output are gitignored.
- Every commit is scanned by gitleaks locally and in CI. After cloning, run:

  ```bash
  ./scripts/install-hooks.sh
  ```
