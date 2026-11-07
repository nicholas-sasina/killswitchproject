# gateway

## What it does
Every agent tool call goes through an AgentCore Gateway that only accepts the agent's own badge,
only allows the tools its scenario role lists, and refuses everything once the badge is revoked.

## How it works
- A badge is one Cognito app client per agent session (`badges.py`); the agent trades it for a token.
- The gateway checks the token; the interceptor then checks the session in DynamoDB on every call.
- Revoked or unknown badges and unlisted tools or recipients are refused; the real caller id is injected.
- Tool output is scanned (`burn_detect`) into the agent's ledger; messages carrying a known key are blocked.
- One tool Lambda serves the mock tools from scenario data; credentials are filled in at read time.

## Key files
| File | What |
|---|---|
| `badges.py` | `create_badge(agent_id, session_id)` / `delete_badge(client_id)`, used by the Burn API |
| `lambdas/interceptor/interceptor.py` | REQUEST/RESPONSE decisions (see `INTERCEPTOR.md`) |
| `lambdas/interceptor/shapes.py` | Reads gateway events, builds pass-through and deny outputs |
| `lambdas/interceptor/sessions.py` | Session lookup (the kill switch) and event writes |
| `lambdas/interceptor/record.py` | RESPONSE: credentials in tool output -> ledger + `recorded` events |
| `lambdas/interceptor/exit_check.py` | REQUEST: blocks credentials in send_message/notify; receiver inherits the ledger |
| `lambdas/interceptor/ledger.py`, `detection.py` | `burn-exposures` writes; `burn_detect` scanner + fingerprint lookup |
| `lambdas/tools/tools.py` | The seven mock tools from `contracts/mcp-tools.json` |
| `lambdas/tools/placeholders.py` | Fills `{{cred:...}}` placeholders from Secrets Manager |
| `scripts/smoke.py` | Live check: badge, tools, ledger, blocked handoff, inheritance, revoke, refused |
| `../infra/burn_infra/identity.py`, `../infra/burn_infra/gateway.py` | BurnIdentity and BurnGateway stacks |

Tool results are one JSON object each: `list_tickets` -> `{"tickets": [{"id", "subject"}]}`,
`read_ticket` -> `{"ticket": {...}}`, `read_config` -> `{"path", "content"}`,
`query_customers` -> `{"customers": [...]}`, `send_message` -> `{"delivered": true, "message_id"}`,
`read_inbox` -> `{"messages": [{"from", "text", "ts"}]}`, `notify` -> `{"sent": true}`.

## How to test it
```bash
cd gateway && uv run pytest -q                       # unit tests (moto, no AWS needed)
cd infra && npx aws-cdk@2 deploy BurnIdentity BurnGateway --require-approval never
cd gateway && uv run python scripts/smoke.py         # live: badge, tools, ledger, handoff, revoke
```
Deploy BurnIdentity before BurnGateway: the gateway reads `/burn/identity/discovery-url` and
`/burn/identity/scope` from SSM. It publishes `/burn/gateway/url`.

## FAQ
- **Deleting the Cognito client revokes the agent, right?** Not on its own. Deleting the app client
  only stops new tokens; a token already issued stays valid at Cognito until it expires (up to 60
  minutes), and access tokens cannot be revoked. The real kill switch is the session `status` in
  DynamoDB, which the interceptor reads with a consistent read on every call. Burn does both.
- **What if an agent re-encodes a key before passing it on?** The exit check undoes common
  encodings and matches fragments, so base64, hex, reversed or split copies are still blocked. A
  novel transformation can slip past, which is why the receiver inherits the sender's ledger anyway.
- **Can an agent pretend to be another agent?** No. Tools never trust an id from the agent: the
  interceptor overwrites `burn_caller_agent_id` with the id of the session behind the token.
