# Demo script (4 minutes)

## Before judging
- Start the agents **at least 15 minutes before** the first judge. CloudTrail delivers events with a lag
  of several minutes, and the blast-radius timeline only shows activity CloudTrail has already recorded.
- Start: `make demo` (or `make demo-step` to hold the reader). Check the dashboard has no MOCK DATA banner.
- Keep a second terminal open in the repo root for the CLI commands below.

## 0:00 - Setup (20 s)
"Three agents work a support queue: a reader, an analyst and a notifier. Each has its own badge and its own
AWS and API keys. Everything they send leaves through one proxy." Show the dashboard: three agents active,
events streaming, the exposure ledger filling as the reader reads config files.

## 0:20 - Moment 1: burn an agent mid-task (60 s)
1. Point at an agent that is mid-ticket (the reader is the clearest). With `make demo-step`, press Enter
   once so it is visibly working.
2. Click **Burn** on that agent in the dashboard (any agent can be chosen, including by a judge).
3. Show the event stream: badge revoked, credentials rotated, shared credentials flagged.
4. Show the agent's container log: `badge revoked/credentials rotated - stopping`
   (`docker compose -f docker-compose.yml -f .run/compose.agents.yml logs <agent-id>`, run in `agents/`).
5. Show the other two agents still working.

## 1:20 - Moment 2: the old key is dead (45 s)
"The key this agent held before the burn is rotated." In the second terminal run
`make replay-old-key AGENT=<agent-id>`. It reads the pre-rotation values from Secrets Manager and
tries them: the mock provider answers 401 and AWS answers `InvalidClientTokenId`. The dashboard
shows the provider's `rejected` event.

## 2:05 - Moment 3: leak test (60 s)
1. In the dashboard open **Leak test** (labelled as a test tool) for the burned agent, or use the leak-test CLI
   (`contracts/leaktest.openapi.yaml`), e.g. with the `base64` encoding.
2. Pick an encoding (or "random"). The credential from the agent's ledger is sent out through the proxy.
3. Show the `blocked` event. Then paste a payload of your own into the free-text box and send it.
4. Say honestly: a novel transformation can slip past; the egress allowlist is the backstop. Quote the eval catch rate.

## 3:05 - Blast radius (55 s)
Open the burned agent's **Blast radius** panel (`GET /burn/<agent-id>/blast-radius`): every CloudTrail
call made with its AWS key, before and after rotation. Close with: "Revoking an agent isn't revoking what it saw.
Burn revoked the agent and killed everything it held or saw, and nothing else."

## If something goes wrong
- Agent already finished its queue: the reader repeats the queue after a pause, or use `--step` for control.
- Blast radius empty: CloudTrail lag. Show the plain list of key uses instead and explain the delay.
- Switching scenario live: `make demo SCENARIO=scenarios/billing-ops.yaml`. Same code, different agents and data.
