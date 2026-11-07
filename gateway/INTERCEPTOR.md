# Gateway interceptor: events, decisions, outputs

One Lambda (`lambdas/interceptor/`) is attached to the AgentCore Gateway at both
interception points. Every MCP message an agent sends passes through it twice:
`REQUEST` before the gateway forwards it to the tool, `RESPONSE` before the reply
goes back to the agent.

## Input event

```jsonc
{
  "interceptorInputVersion": "1.0",
  "mcp": {
    "gatewayRequest": {
      "path": "/mcp", "httpMethod": "POST",
      "headers": { "Authorization": "Bearer <jwt>", "X-Amzn-Trace-Id": "Root=...", ... },
      "body": { "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": { "name": "burn-tools___read_config", "arguments": { "path": "..." } } }
    },
    "gatewayResponse": null            // REQUEST: present but null
    // RESPONSE: { "statusCode": 200, "headers": {}, "body": { "jsonrpc": "2.0", "id": 3, "result": {...} } }
  }
}
```

- The point is `RESPONSE` when `gatewayResponse` is not null (the key exists at `REQUEST` too).
- Header names arrive in mixed case, so they are matched case-insensitively.
- The gateway has already verified the token's signature, expiry and `burn/tools` scope.
  Calls without a valid token get 401/403 from the gateway and never reach the interceptor.

## Outputs

| Decision | Output `mcp` field |
|---|---|
| Forward the request (maybe edited) | `{"transformedGatewayRequest": {"body": <body>}}` |
| Answer it ourselves (deny) | `{"transformedGatewayResponse": {"statusCode": 200, "body": <JSON-RPC reply>}}` |
| Return the reply (maybe edited) | `{"transformedGatewayResponse": {"statusCode": <code>, "body": <body>}}` |

Every output also carries `"interceptorOutputVersion": "1.0"`. A pass-through must echo the body:
an empty `mcp` object is not a pass-through.

Denials always start with `BURN:` so agents, logs and the `RESPONSE` step can recognize them:
- `tools/call`: a normal tool result, `{"isError": true, "content": [{"type": "text", "text": "BURN: access refused: ..."}]}`.
  Agent frameworks hand this to the model as a readable tool error.
- Any other method: a JSON-RPC error, `{"code": -32001, "message": "BURN: access refused: ..."}`.

## REQUEST decisions

| Check | Result when it fails |
|---|---|
| JSON-RPC notification (no `id`) | always forwarded: it runs no tool and gets no reply |
| Token's `client_id` maps to a session in `burn-sessions` (GSI `by_client`) | `refused`, "unknown badge" (event `agent_id` = `system`) |
| Session `status` is `active` (consistent read on every call) | `refused`, "badge revoked" |
| `tools/call`: tool is in the session's `tools` | `refused`, "tool not allowed for this agent: <tool>" |
| `send_message`: `to` is in the session's `send_to` | `refused`, "recipient not allowed: <id>" (`target_agent_id` = `to`) |
| `send_message` / `notify`: arguments carry no credential Burn minted (exit check) | `blocked`, "BURN: blocked: the message contains a credential" |

### Exit check
`send_message` (to another agent) and `notify` (to a customer or channel) are the tools that move
text out of an agent. Their arguments are scanned with `burn_detect`, which undoes common encodings
(base64, hex, URL, reversal, separators, ...) and also matches fragments by shingle coverage. Any
credential Burn minted, current or rotated, blocks the call: one `blocked` event per credential
(source `handoff` for send_message, `egress` for notify) with the `encoding` that was undone.
Values that only look like a credential format are not blocked here.

A `send_message` that passes still carries whatever the sender knows, in ways no scanner can see,
so the receiver inherits the sender's whole ledger: each entry is copied with `source: handoff`,
`via: <sender>`, and an `inherited` event is written. Burning either agent then covers everything
both may have seen.

A forwarded `tools/call` is edited in two ways:
- `burn_caller_agent_id` is set to the session's agent id, overwriting any value the agent sent.
  Tools get no caller identity from the gateway, so this is the only one they trust.
- The tool name gets the gateway target prefix (`list_tickets` -> `burn-tools___list_tickets`).
  Agents only ever see the plain names from `contracts/mcp-tools.json`.

## RESPONSE

- Replies that are Burn denials pass through untouched (`RESPONSE` still fires after a `REQUEST` deny).
- `tools/list`: tools are renamed to their plain names and filtered to the session's `tools`.
- `tools/call`: the result text is scanned with `burn_detect` before the agent sees it. Each
  credential found (one per credential, exact matches preferred) is added to the agent's ledger
  and announced with a `recorded` event. The reply itself is passed on unchanged: Burn records
  exposure, it does not redact.
- Output larger than the scanner's limit (256 KiB) is withheld: the agent gets a `BURN:` denial
  and a `refused` event (source `tool_response`) is written. Burn does not hand over what it
  cannot check.

## Ledger row (`burn-exposures`)

```json
{"agent_id": "agent-alpha", "secret_ref_or_fp": "burn/agent-alpha/api_key",
 "secret_ref": "burn/agent-alpha/api_key", "fingerprint": "<64 hex>", "secret_type": "api_key",
 "owner": "agent", "owner_agent_id": "agent-alpha",
 "source": "tool_response", "via": "read_config",
 "first_seen": "2026-11-07T20:50:33.201Z", "last_seen": "...", "seen_in": ["<trace id>#<rpc id>", "..."]}
```

- Credentials that match only a known format (not minted by Burn) have no `secret_ref`; their sort
  key is `fp#<fingerprint>` and `owner` is `unknown`.
- `source`, `via` and `first_seen` keep the first sighting; later sightings only update `last_seen`
  and `seen_in`.
- The gateway may retry an interceptor call, so writes are keyed by the call
  (`X-Amzn-Trace-Id` + JSON-RPC id): a write whose call is already in `seen_in` is skipped,
  together with its event. Reading the same file again in a new call does write a new event.

## Session row (written by the Burn API at `POST /sessions`)

```json
{"session_id": "sess_...", "agent_id": "agent-...", "client_id": "<cognito app client id>",
 "status": "active", "tools": ["list_tickets", "..."], "send_to": ["agent-..."]}
```

A missing `tools` list means no tools are allowed (fail closed).

## Events written to `burn-events`

All built with `burn_contracts.events.make_event`, stored with `stream = "all"`.

| action | source | fields |
|---|---|---|
| `refused` | `gateway` | `agent_id`, `session_id`, `tool`, `target_agent_id` (send_message), `detail` |
| `recorded` | `tool_response` | `agent_id`, `session_id`, `tool`, `secret_ref`, `fingerprint`, `secret_type`, `owner`, `owner_agent_id`, `detail` (e.g. "shared credential seen in read_config output") |
| `refused` | `tool_response` | output too large to check |
| `blocked` | `handoff` / `egress` | `agent_id` = sender, `tool`, `secret_ref`, `fingerprint`, `secret_type`, `owner`, `owner_agent_id`, `encoding`, `target_agent_id` = receiver (send_message), `destination` = channel (notify) |
| `inherited` | `handoff` | `agent_id` = receiver, `target_agent_id` = receiver, `session_id` = sender's session, `tool` = send_message, credential fields, `detail` "inherited from <sender> via send_message" |

No event or ledger row ever holds a credential value.
