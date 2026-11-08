"""Mock tools behind the AgentCore Gateway (one Lambda target, contracts/mcp-tools.json).

The gateway invokes this Lambda with only the tool arguments as the event;
the tool name comes in the Lambda client context. Each tool returns one JSON
object, which the gateway hands to the agent as a single text content item
(agents parse it, so the shapes below are part of the contract). The interceptor adds
burn_caller_agent_id and burn_caller_session_id, the real caller and its session, so tools
that act "as" an agent (messages) never trust an id the agent typed, and {{canary}} is filled
with the calling session's own canary.
"""
import contextvars
import logging

import placeholders
import store

CALLER_ARG = "burn_caller_agent_id"
SESSION_ARG = "burn_caller_session_id"  # injected by the interceptor; used only to fill {{canary}}
TOOL_NAME_KEY = "bedrockAgentCoreToolName"
TOOL_SEPARATOR = "___"

_session: contextvars.ContextVar = contextvars.ContextVar("burn_caller_session", default=None)
log = logging.getLogger()
log.setLevel(logging.INFO)


def list_tickets(args: dict, caller: str | None) -> dict:
    tickets = sorted(store.scenario_items("ticket#"), key=lambda t: t["id"])
    return {"tickets": [{"id": t["id"], "subject": t["subject"]} for t in tickets]}


def read_ticket(args: dict, caller: str | None) -> dict:
    ticket = store.scenario_item(f"ticket#{args.get('ticket_id')}")
    if not ticket:
        return {"error": f"ticket not found: {args.get('ticket_id')}"}
    return {"ticket": ticket}


def read_config(args: dict, caller: str | None) -> dict:
    file = store.scenario_item(f"file#{args.get('path')}")
    if not file:
        return {"error": f"file not found: {args.get('path')}"}
    return {"path": file["path"], "content": placeholders.resolve(file["content"], caller, _session.get())}


def query_customers(args: dict, caller: str | None) -> dict:
    query = str(args.get("query") or "").strip().lower()
    matches = []
    for customer in store.scenario_items("customer#"):
        fields = (customer.get("id", ""), customer.get("name", ""), customer.get("email", ""))
        if query and any(query in f.lower() for f in fields):
            found = dict(customer)
            if "notes" in found:
                found["notes"] = placeholders.resolve(found["notes"], caller, _session.get())
            matches.append(found)
    return {"customers": sorted(matches, key=lambda c: c["id"])}


def send_message(args: dict, caller: str | None) -> dict:
    # Allowed recipients are enforced by the interceptor (the agent's send_to list).
    message_id = store.put_message(args["to"], caller, str(args.get("text", "")))
    return {"delivered": True, "message_id": message_id}


def read_inbox(args: dict, caller: str | None) -> dict:
    messages = store.take_messages(caller)
    return {"messages": [{"from": m["from_agent"], "text": m["text"], "ts": m["ts"]} for m in messages]}


def notify(args: dict, caller: str | None) -> dict:
    # Nothing is really sent. The notification is kept in an outbox so the
    # demo can show what would have gone out.
    store.put_message(store.OUTBOX, caller, str(args.get("text", "")), channel=str(args.get("channel", "")))
    return {"sent": True}


TOOLS = {f.__name__: f for f in (
    list_tickets, read_ticket, read_config, query_customers, send_message, read_inbox, notify,
)}
NEEDS_CALLER = {"send_message", "read_inbox", "notify"}


def tool_name(context) -> str:
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    return str(custom.get(TOOL_NAME_KEY, "")).split(TOOL_SEPARATOR, 1)[-1]


def handler(event, context):
    name = tool_name(context)
    args = dict(event or {})
    caller = args.pop(CALLER_ARG, None)
    _session.set(args.pop(SESSION_ARG, None))
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"unknown tool: {name}"}
    if name in NEEDS_CALLER and not caller:
        return {"error": "caller identity missing (call this tool through the gateway)"}
    log.info("tool=%s caller=%s", name, caller)  # arguments may carry secrets: never logged
    return tool(args, caller)
