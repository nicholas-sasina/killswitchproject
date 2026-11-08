"""Read AgentCore Gateway interceptor events and build interceptor outputs.

The gateway calls one Lambda at two points for every MCP message:
REQUEST (before the tool runs) and RESPONSE (before the reply reaches the
agent). Exact event and output shapes are in gateway/INTERCEPTOR.md.
"""
import base64
import json

OUTPUT_VERSION = "1.0"
DENY_PREFIX = "BURN:"
TOOL_SEPARATOR = "___"  # the gateway names tools "<targetName>___<tool>"
REFUSED_RPC_CODE = -32001  # JSON-RPC "server error" range, used for non-tool denials


def _mcp(event: dict) -> dict:
    return event.get("mcp") or {}


def is_response(event: dict) -> bool:
    # At REQUEST the key "gatewayResponse" exists with a null value,
    # so test the value, not whether the key is present.
    return _mcp(event).get("gatewayResponse") is not None


def request_body(event: dict) -> dict:
    return (_mcp(event).get("gatewayRequest") or {}).get("body") or {}


def gateway_response(event: dict) -> dict:
    return _mcp(event).get("gatewayResponse") or {}


def header(event: dict, name: str) -> str | None:
    """Header value, matched case-insensitively (the gateway mixes cases)."""
    headers = (_mcp(event).get("gatewayRequest") or {}).get("headers") or {}
    wanted = name.lower()
    return next((v for k, v in headers.items() if k.lower() == wanted), None)


def jwt_claims(event: dict) -> dict | None:
    """Claims of the caller's access token.

    No signature check here on purpose: the gateway's JWT authorizer already
    verified signature, issuer, expiry and scope, and rejects bad tokens
    before the interceptor is called.
    """
    auth = header(event, "authorization") or ""
    token = auth.split(" ", 1)[-1]
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        return None


def short_tool_name(name: str) -> str:
    """'burn-tools___read_config' -> 'read_config'."""
    return name.split(TOOL_SEPARATOR, 1)[-1]


def call_key(event: dict) -> str:
    """Identifies one MCP call across interceptor retries: trace id + JSON-RPC id."""
    trace = header(event, "x-amzn-trace-id") or "no-trace"
    return f"{trace}#{request_body(event).get('id')}"


def pass_request(body: dict) -> dict:
    # Pass-through must echo the body: an empty "mcp" object is a parse error.
    return {"interceptorOutputVersion": OUTPUT_VERSION, "mcp": {"transformedGatewayRequest": {"body": body}}}


def pass_response(status_code: int, body) -> dict:
    return {
        "interceptorOutputVersion": OUTPUT_VERSION,
        "mcp": {"transformedGatewayResponse": {"statusCode": status_code, "body": body}},
    }


def deny(body: dict, reason: str) -> dict:
    """Answer the call ourselves; the tool is never invoked.

    tools/call gets a normal tool result with isError, which agent frameworks
    hand to the model as a readable error. Other methods get a JSON-RPC error.
    """
    text = f"{DENY_PREFIX} {reason}"
    reply = {"jsonrpc": "2.0", "id": body.get("id")}
    if body.get("method") == "tools/call":
        reply["result"] = {"isError": True, "content": [{"type": "text", "text": text}]}
    else:
        reply["error"] = {"code": REFUSED_RPC_CODE, "message": text}
    return pass_response(200, reply)


def is_burn_denial(body) -> bool:
    """True for replies Burn itself produced at REQUEST (RESPONSE still fires for them)."""
    if not isinstance(body, dict):
        return False
    message = (body.get("error") or {}).get("message") or ""
    texts = result_texts(body)
    return message.startswith(DENY_PREFIX) or (bool(texts) and texts[0].startswith(DENY_PREFIX))


def result_texts(body) -> list[str]:
    """Text parts of a tools/call result (what the agent will read)."""
    if not isinstance(body, dict):
        return []
    content = (body.get("result") or {}).get("content") or []
    return [c["text"] for c in content if isinstance(c, dict) and isinstance(c.get("text"), str)]


def _strings(node) -> list[str]:
    """Every string in a JSON value, object keys included."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def other_strings(body) -> list[str]:
    """Every string of a tools/call reply that result_texts does not return: the other fields of each
    content part (an embedded resource's text, for example), structuredContent, _meta, and a JSON-RPC error.
    The agent reads these too, so they are scanned as well."""
    if not isinstance(body, dict):
        return []
    result = body.get("result")
    rest: list = [body.get("error")]
    if isinstance(result, dict):
        rest.append({k: v for k, v in result.items() if k != "content"})
        content = result.get("content") or []
        if isinstance(content, list):
            rest += [{k: v for k, v in c.items() if k != "text"} if isinstance(c, dict) and isinstance(c.get("text"), str)
                     else c for c in content]
        else:
            rest.append(content)
    else:
        rest.append(result)
    return _strings(rest)
