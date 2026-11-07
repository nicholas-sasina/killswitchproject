# Demo script (4 minutes)

## Before judging
- Start the agents **at least 15 minutes before** the first judge. CloudTrail delivers events with a lag
  of several minutes, and the blast-radius timeline only shows activity CloudTrail has already recorded.
- Start, each in its own terminal: `make demo` (or `make demo-step` to hold the reader) and `make dashboard`.
  Check the dashboard has no MOCK DATA banner.
- Keep another terminal open in the repo root for the CLI commands below (`make burn ARGS="agents"` lists the
  agents and their status).

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
   (`docker logs burn-demo-<agent-id>-1`).
5. Show the other two agents still working.

## 1:20 - Moment 2: the old key is dead (45 s)
"The key this agent held before the burn is rotated." In the second terminal run
`make replay-old-key AGENT=<agent-id>`. It reads the pre-rotation values from Secrets Manager and
tries them: the mock provider answers 401 and AWS answers `InvalidClientTokenId`. The dashboard
shows the provider's `rejected` event.

## 2:05 - Moment 3: exit check (60 s)
1. Point at the real `blocked` event with source `handoff`: the reader tried to send its API key to the
   analyst through `send_message`. Show the explanation, such as `send_message blocked: known credential`.
2. Show the outbound side from the operator terminal: `make exit-check-test AGENT=agent-notifier ENCODING=base64`.
   The notifier was never burned, but it inherited the reader's keys through the analyst; the proxy refuses to let
   them leave (`blocked: credential_detected`) and a `blocked` event with source `egress` appears in the stream.
3. Run it for the burned agent: the proxy refuses it outright (`session_inactive`).
4. Say honestly: a novel transformation can slip past; the egress allowlist is the backstop. Quote the eval catch rate.

## 3:05 - Blast radius (55 s)
Open the burned agent's **Blast radius** panel (`GET /burn/<agent-id>/blast-radius`): every CloudTrail
call made with its AWS key, before and after rotation. Close with: "Revoking an agent isn't revoking what it saw.
Burn revoked the agent and killed everything it held or saw, and nothing else."

## If something goes wrong
- Agent already finished its queue: the reader repeats the queue after a pause, or use `--step` for control.
- Blast radius empty: CloudTrail lag. Show the plain list of key uses instead and explain the delay.
- Dashboard unresponsive: burn from the terminal with `make burn ARGS="kill <agent-id> --yes"`, then
  `make burn ARGS="ledger <agent-id>"` and `make burn ARGS="blast-radius <agent-id>"`.
- Switching scenario live: `make demo SCENARIO=scenarios/billing-ops.yaml`. Same code, different agents and data.
