# Burn

**Revoking an agent isn't revoking what it saw.**

Burn contains a compromised AI agent in one click: it revokes the agent's badge and kills every
credential the agent held or saw, without touching any other agent or service.

1. **Per-agent credentials.** Every agent gets its own copy of each credential it needs, so burning
   one agent never breaks another, and a leaked key traces to exactly one agent.
2. **Exposure ledger.** Every tool response passes through the Gateway; live credentials found in it
   are recorded per agent (fingerprints only, never raw values).
3. **Exit check.** All agent traffic leaves through an egress proxy that blocks known credentials,
   including re-encoded copies and copies split across requests.
4. **Burn.** Revoke the badge, rotate exactly that agent's credentials, flag shared credentials it
   saw, and pull CloudTrail history for its AWS keys (blast radius).

## Repo layout (one folder = one owner)

| Folder | What lives there |
|---|---|
| `contracts/` | JSON Schema + OpenAPI + MCP tool definitions. Source of truth for every lane. |
| `mocks/` | Mock Burn API and mock MCP tool server for development. |
| `infra/` | AWS CDK app. One stack per component. |
| `gateway/` | AgentCore Gateway, request/response interceptor Lambdas, mock tool Lambdas. |
| `minter/` | Credential minter, rotation, mock API provider, CloudTrail query. |
| `detection/` | Shared detector library and the egress proxy (mitmproxy addon). |
| `burn_api/` | Burn API Lambdas: sessions, burn, blast radius, events. |
| `agents/` | Strands agents, scenarios, docker-compose. |
| `dashboard/` | React + Vite dashboard. |
| `eval/` | Catch rate, false positives, latency, load test, cost. |
| `docs/` | Architecture, runbook, demo script. |

Live build status: [`STATUS.md`](STATUS.md).

## Secrets policy (this repo is public)

- Burn mints every credential it uses at runtime, with random values. Nothing real is checked in.
- `.env`, `.run/`, certs, CDK outputs and raw eval output are gitignored.
- Every commit is scanned by gitleaks locally and in CI. After cloning, run:

  ```bash
  ./scripts/install-hooks.sh
  ```
