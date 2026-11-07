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

A forwarded `tools/call` is edited in two ways:
- `burn_caller_agent_id` is set to the session's agent id, overwriting any value the agent sent.
  Tools get no caller identity from the gateway, so this is the only one they trust.
- The tool name gets the gateway target prefix (`list_tickets` -> `burn-tools___list_tickets`).
  Agents only ever see the plain names from `contracts/mcp-tools.json`.

## RESPONSE

- Replies that are Burn denials pass through untouched (`RESPONSE` still fires after a `REQUEST` deny).
- `tools/list`: tools are renamed to their plain names and filtered to the session's `tools`.

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
