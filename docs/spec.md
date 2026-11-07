# Burn: build spec (MVP)

The original orchestrator spec for the hackathon build, kept in the repo so every lane and every
human owner reads the same thing. Where the build deviates, `STATUS.md` (Decisions) and
`contracts/README.md` win.

## 0. Hard constraints

- **Deadlines (Pacific time):** feature freeze 4:00 AM Sunday Nov 8. Devpost submission due 9:00 AM
  Sunday. Judging starts 9:15 AM.
- **Hackathon rules:** all code is written during the event, in this repo. No code copied from other
  projects. Open-source libraries are fine.
- **Judges will question the humans on the code.** Lack of understanding can disqualify the team:
  - Simple code, small files, clear names, comments that explain *why*.
  - Every top-level folder gets a `README.md` (format in section 10).
  - Prefer boring, well-known libraries over clever code.
- **Credentials:** only test credentials Burn itself mints, or obviously fake values. Never print, log,
  or commit real secrets. `.env` files are gitignored. The ledger stores fingerprints, never raw values.
- **AWS:** temporary hackathon event account, `us-east-1` only. Every component gets its own
  least-privilege IAM role. No wildcard permissions on execution roles.
- **Ask the human (Oliver) before:** anything that costs real money beyond normal demo usage, deleting
  AWS resources you did not create, changing account-level settings, or any step you cannot undo.

### No hard-coding (non-negotiable)

The demo must be real every time. Judges should be able to change what happens and watch Burn handle it.

1. Product code never contains demo-specific values: no agent IDs, credential values, expected results,
   or special cases for the demo scenario.
2. Every credential is minted at runtime with random values. A fresh run produces fresh values.
3. Agents make real model calls on every run. Nothing replays recorded responses.
4. The dashboard shows only live API data in demo mode. Mocks exist for development only, behind one
   flag that the runbook checks is off.
5. Detection never matches demo strings specifically. Eval payloads are generated at runtime from
   freshly minted credentials.
6. Any agent can be burned at any moment, and the result must be correct for whichever one is chosen.
7. Scenario inputs (tickets, files the agents read) live in data files. A second scenario must run
   through the same code with no code changes.

## 1. What Burn is

**One line:** Burn contains a compromised AI agent in one click: it revokes the agent and kills every
credential the agent held or saw, without touching any other agent or service.

**Pitch:** revoking an agent isn't revoking what it saw.

1. **Per-agent credentials.** Every agent gets its own copies of the credentials it needs, so burning one
   agent never breaks another, and any leaked key traces to exactly one agent.
2. **Exposure ledger.** Every tool response passes through the Gateway; live credentials found in it are
   recorded per agent (fingerprint only).
3. **Exit check.** All outbound traffic and agent-to-agent messages pass through an egress proxy that
   blocks known credentials from leaving, including after common re-encoding or when spread across a
   session's recent requests.
4. **Burn.** One action: revoke the agent's badge, rotate exactly that agent's credentials, flag shared
   credentials it saw, and pull CloudTrail history for its AWS keys (blast radius).

**Tracks:** A10 Prompt 1 (Agentic Identity) and the AWS track. Prompt 1's required demo: authenticate an
agent, allow selected MCP tools, revoke access mid-task, block subsequent requests.

## 2. Architecture

```
            ┌───────────────────────── AWS ─────────────────────────┐
 Agents ──tool calls──► AgentCore Gateway ──writes──► DynamoDB (ledger, events)
 (Strands,  ◄─own keys── Credential minter ──stores──► Secrets Manager
  Docker)                    ▲ rotate
   │ all traffic             │
   ▼                         │
 Egress proxy ──detections──► Burn API ──queries──► CloudTrail
                                  │
 Dashboard (React) ◄──polls /events (API Gateway + Lambda)
 Cognito: one badge per agent session
            └───────────────────────────────────────────────────────┘
```

| Component | Runs on | Job |
|---|---|---|
| Agents | Strands Agents (Python) on Bedrock, in Docker | Do the demo task; each holds its own badge and credentials |
| AgentCore Gateway | Gateway + pre-call and post-call Lambda interceptors | Pre-call: refuse revoked badges. Post-call: scan tool responses, write the ledger |
| Mock tools | Lambda targets behind the Gateway | `read_ticket`, `read_config`, `query_customers`, `send_message`, `notify` (+ `list_tickets`, `read_inbox`) |
| Credential minter | Lambda + IAM + Secrets Manager | Issue and rotate per-agent credentials |
| Mock API provider | API Gateway + Lambda | Accepts only the current version of each per-agent API key |
| Egress proxy | mitmproxy addon (Python) in Docker | The agents' only network route; exit check; blocks 169.254.169.254 |
| Burn API | API Gateway + Lambda | Revoke, rotate, blast radius |
| Ledger / events | DynamoDB | Fingerprints and events; feeds the dashboard |
| Badges | Cognito (client credentials) | One badge per agent session |
| Dashboard | React + Vite | Agents, live ledger, Burn button, blast-radius timeline |
| Infra | AWS CDK (Python) | Everything reproducible from one command |

**Credentials in the MVP:**
- **Per-agent API key** for the mock API provider, in Secrets Manager at `burn/<agent_id>/api_key`.
- **Per-agent AWS access key** on a per-agent demo IAM user with a narrow policy. Rotation = create new
  key, deactivate old. Blast radius = CloudTrail `LookupEvents` by `AccessKeyId`.
- Stretch only: per-agent STS sessions, per-agent database users.

**CloudTrail note:** event history can lag several minutes. The demo must generate the burned agent's AWS
activity well before the Burn moment. Use management events, which appear in event history without
extra trail setup.

## 3. Core flows

1. **Agent start:** dashboard (or CLI) starts a session → Cognito issues a badge → minter creates the
   agent's credentials and returns references.
2. **Tool call:** agent calls a tool via the Gateway with its badge → pre-call Lambda checks the badge in
   DynamoDB (`active` or `revoked`) → tool runs → post-call Lambda scans the response, writes `recorded`
   ledger events.
3. **Exit check:** every outbound request and every `send_message` payload is normalized and compared
   against the ledger → match = blocked + `blocked` event. The receiving agent of a `send_message`
   inherits the sender's ledger entries.
4. **Burn:** `POST /burn` → badge marked revoked (every later call refused at the Gateway and the proxy)
   → minter rotates all credentials owned by that agent → shared credentials it saw are flagged →
   blast-radius job queries CloudTrail → events stream to the dashboard.

## 4. Contracts

See `/contracts` (source of truth). Dashboard polls `/events` once per second. No WebSockets.

## 5. Detection (defensive and measurable)

- **Credentials Burn minted:** recognized exactly; the minter registers each value's fingerprint at
  creation.
- **Other credentials in tool output:** a small set of known-format detectors (AWS access key IDs, the
  mock provider's key prefix, connection strings). An existing open-source detector library is fine.
- **Exit check normalization:** before comparing, decode common encodings of payload segments, check a
  per-session window of recent requests together, and parse URLs and query strings. Compare against
  ledger fingerprints.
- **Agent-to-agent messages:** same check; receiver inherits sender's entries.
- **Known limit:** a novel transformation can slip past. The egress allowlist is the backstop. Report the
  real catch rate.

## 6. Demo scenario (repeatable, never canned)

Repeatable means the structure is predictable; it does not mean results are faked.

- **Agents:** three Strands agents on a fixed graph (not a free-form swarm): a reader (tickets and files),
  an analyst (customer lookups), a notifier (notifications). Temperature 0, real model calls.
- **Scenario data:** `/agents/scenarios/*.yaml`. Files are rendered with that run's freshly minted test
  credentials, so the reader really passes through its own live keys and the ledger lights up from real
  detections. Ship at least two scenarios.
- **Moment 1:** the presenter (or a judge) picks any agent and hits Burn mid-task. Its badge is refused
  and its keys rotate; the other agents keep working.
- **Moment 2:** the pre-rotation copy of the burned agent's API key, captured earlier from the run, is sent
  to the mock API provider and rejected.
- **Moment 3:** a "Leak test" control (dashboard button and CLI) takes a credential currently in the chosen
  agent's ledger, applies an encoding chosen by the presenter or at random, and sends it out through the
  proxy; it is blocked and logged. A free-text box lets anyone paste their own payload. Labeled clearly
  as a test tool.
- **Then:** blast-radius timeline from CloudTrail for the burned agent's AWS key.
- `make demo` (or one script) resets state, mints fresh credentials and starts the agents, plus a
  `--step` mode for presenting live.

## 7. Eval (`/eval`)

| Metric | Method |
|---|---|
| Catch rate | ~30 outbound test payloads carrying planted test credentials in varied forms; share blocked |
| False-positive rate | ~30 normal payloads with no credentials; share wrongly blocked |
| Added latency | Median and p95 per tool call, with and without Burn's interceptors and proxy |
| Scale (AWS track) | Load test with ~1,000 simulated agents against the Gateway path; throughput, p50/p95 |
| Cost | AWS cost per 1M tool calls from list prices |

Output `eval/results.json` and a short `eval/RESULTS.md`. Never tune detectors on the eval set.

## 9. Phases

- **Phase 0:** contracts, mocks, infra skeleton, hour-one checks.
- **Phase 1 (Prompt 1 demo):** badges, Gateway with mock tools, revoke, dashboard showing a blocked call.
  This alone must be a complete, submittable demo.
- **Phase 2:** per-agent credentials, exposure ledger, Burn rotating only that agent's credentials.
- **Phase 3:** exit check incl. transformed copies and agent-to-agent messages; blast-radius timeline.
- **Phase 4:** eval, load test, cost numbers, docs, demo polish. Freeze at 4:00 AM.

**Cut order if behind:** agent-to-agent exit check → multi-request window → CloudTrail timeline (keep a
plain list of key uses) → load test. **Never cut:** badges + revoke, per-agent credentials, the ledger,
Burn rotating only that agent's copies.

## 10. Handoff format

Every top-level folder has a `README.md` with these sections:
- **What it does** (2 lines)
- **How it works** (5 lines max)
- **Key files**
- **How to test it**
- **If a judge asks...** (2 to 3 likely questions with plain answers)

## 11. Non-goals

Preventing exposure in the first place; prompt-injection detection; real third-party SaaS integrations;
production hardening, multi-tenancy, dashboard auth beyond the demo.
