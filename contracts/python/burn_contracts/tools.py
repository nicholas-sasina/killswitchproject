"""Per-tool facts from contracts/mcp-tools.json that the Gateway needs at runtime.

Lambdas are bundled with this package but not with the JSON file, so the two fields are
mirrored here; tests/test_contracts.py fails if they drift from mcp-tools.json.
"""
import hashlib

RESOURCE_ARG = {"read_ticket": "ticket_id", "read_config": "path"}
ACCEPTS_SECRET_REFS: dict[str, tuple[str, ...]] = {}
NEVER_RESOLVE = frozenset({"send_message", "notify"})  # tools that move text out of an agent
MAX_RESOURCE_LENGTH = 200
HASHED_PREFIX = "sha256:"


def resource_ref(tool: str, resource) -> str:
    """'<tool>:<resource>' when the resource is printable, at most MAX_RESOURCE_LENGTH long and does not
    itself start with 'sha256:'; otherwise '<tool>:sha256:<hex of the resource>'. Never lossy: two
    different resources never share a source_ref."""
    text = str(resource)
    if text.isprintable() and len(text) <= MAX_RESOURCE_LENGTH and not text.startswith(HASHED_PREFIX):
        return f"{tool}:{text}"
    return f"{tool}:{HASHED_PREFIX}{hashlib.sha256(text.encode()).hexdigest()}"


def source_ref(tool: str, arguments: dict | None) -> str:
    """What a tool call read: '<tool>:<resource>' (e.g. read_ticket:T-1001), or just the tool."""
    arg = RESOURCE_ARG.get(tool)
    value = (arguments or {}).get(arg) if arg else None
    if not isinstance(value, (str, int)) or isinstance(value, bool) or str(value) == "":
        return tool
    return resource_ref(tool, value)


def requested_source_ref(source_ref: str, tool: str | None) -> str:
    """The source_ref a by-source request names: as given, or, when `tool` is set and source_ref is only
    the resource, that resource as the Gateway records it (so a long or unusual resource still matches)."""
    if tool is None or source_ref == tool or source_ref.startswith(tool + ":"):
        return source_ref
    return resource_ref(tool, source_ref)


def resolvable_args(tool: str) -> tuple[str, ...]:
    """Arguments of `tool` in which a reference may be swapped for the real value."""
    return () if tool in NEVER_RESOLVE else ACCEPTS_SECRET_REFS.get(tool, ())
