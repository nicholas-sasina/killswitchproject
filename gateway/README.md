# gateway

## What it does
Every agent tool call goes through an AgentCore Gateway that only accepts the agent's own badge,
only allows the tools its scenario role lists, and refuses everything once the badge is revoked.

## How it works
- A badge is one Cognito app client per agent session (`badges.py`); the agent trades it for a token.
- The gateway checks the token; the interceptor then checks the session in DynamoDB on every call.
- Revoked or unknown badges and unlisted tools or recipients are refused; the real caller id is injected.
- Tool output is scanned (`burn_detect`) into the agent's ledger; messages carrying a known key are blocked.
- **Secret references** (role field `references`, default `replace`): every located credential Burn minted is
  swapped for `{{burn:ref:<secret_ref>}}` before the agent sees it (in a connection string only the password),
  so the agent never holds the value and no encoding of what it sends can leak it. The result is scanned again;
  a credential that cannot be located exactly, or a copy that survives, withholds the whole response
  (`refused` event). Canaries are never replaced. `report` records what would change; `off` does neither.
  Ledger rows say `referenced` or `seen`, and record `source_ref` (`<tool>:<resource>`).
- **Fingerprint cache**: the interceptor keeps every fingerprint in memory and re-reads only the minter's
  version item per call, reloading when it changes. Per call that is the session row plus one item, at any
  output size (36 B, 1 KiB and 16 KiB all measure 2 GetItem in `tests/test_fingerprint_cache.py`).
- Tools that declare `accepts_secret_refs` get permitted references resolved to the value at REQUEST
  (own credentials or ones in the agent's ledger; never canaries, the badge, `send_message` or `notify`).
- One tool Lambda serves the mock tools from scenario data; credentials are filled in at read time.

## Key files
| File | What |
|---|---|
| `badges.py` | `create_badge(agent_id, session_id)` / `delete_badge(client_id)`, used by the Burn API |
| `lambdas/interceptor/interceptor.py` | REQUEST/RESPONSE decisions (see `INTERCEPTOR.md`) |
| `lambdas/interceptor/shapes.py` | Reads gateway events, builds pass-through and deny outputs |
| `lambdas/interceptor/sessions.py` | Session lookup (the kill switch) and event writes |
| `lambdas/interceptor/record.py` | RESPONSE: credentials in tool output -> references, ledger + `recorded` events |
| `lambdas/interceptor/redact.py` | Replace located credentials with references (pure functions) |
| `lambdas/interceptor/resolve.py` | REQUEST: resolve permitted references in declared tool arguments |
| `lambdas/interceptor/exit_check.py` | REQUEST: blocks credentials in send_message/notify; receiver inherits the ledger |
| `lambdas/interceptor/ledger.py`, `detection.py` | `burn-exposures` writes; `burn_detect` scanner + in-memory fingerprint cache |
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
- **Why replace instead of just recording?** A recorded credential can still leave in an encoding a
  scanner does not undo. A reference has no value to leak: whatever the agent does with it, the secret
  stays in Secrets Manager. Recording still happens, so a burn knows what the agent saw.
- **Does the agent lose anything?** It reads `{{burn:ref:...}}` where the secret was. Its own provider key
  comes from its environment, not from tool output, and tools that need a secret can accept a reference.
- **Deleting the Cognito client revokes the agent, right?** Not on its own. Deleting the app client
  only stops new tokens; a token already issued stays valid at Cognito until it expires (up to 60
  minutes), and access tokens cannot be revoked. The real kill switch is the session `status` in
  DynamoDB, which the interceptor reads with a consistent read on every call. Burn does both.
- **What if an agent re-encodes a key before passing it on?** The exit check undoes common
  encodings and matches fragments, so base64, hex, reversed or split copies are still blocked. A
  novel transformation can slip past, which is why the receiver inherits the sender's ledger anyway.
- **Can an agent pretend to be another agent?** No. Tools never trust an id from the agent: the
  interceptor overwrites `burn_caller_agent_id` with the id of the session behind the token.
