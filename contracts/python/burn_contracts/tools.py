"""Per-tool facts from contracts/mcp-tools.json that the Gateway needs at runtime.

Lambdas are bundled with this package but not with the JSON file, so the two fields are
mirrored here; tests/test_contracts.py fails if they drift from mcp-tools.json.
"""
RESOURCE_ARG = {"read_ticket": "ticket_id", "read_config": "path"}
ACCEPTS_SECRET_REFS: dict[str, tuple[str, ...]] = {}
NEVER_RESOLVE = frozenset({"send_message", "notify"})  # tools that move text out of an agent
MAX_RESOURCE_LENGTH = 200


def source_ref(tool: str, arguments: dict | None) -> str:
    """What a tool call read: '<tool>:<resource>' (e.g. read_ticket:T-1001), or just the tool."""
    arg = RESOURCE_ARG.get(tool)
    value = (arguments or {}).get(arg) if arg else None
    if not isinstance(value, (str, int)) or str(value) == "":
        return tool
    text = "".join(c for c in str(value) if c.isprintable())[:MAX_RESOURCE_LENGTH]
    return f"{tool}:{text}"


def resolvable_args(tool: str) -> tuple[str, ...]:
    """Arguments of `tool` in which a reference may be swapped for the real value."""
    return () if tool in NEVER_RESOLVE else ACCEPTS_SECRET_REFS.get(tool, ())
