"""What `burn run` does when it is given no role or no join token: name the role, then get it or make it.

A role named after the command lets `burn run claude` start without a role made beforehand. A signed-in person
needs no join token either: burn run reuses the role, or creates it with full access, and mints a one-use token
for itself. Every instance still has a role, so it is burnable and its badge is cleaned up like any other.
"""
import re
from pathlib import PurePath

from burn_contracts import names

from .api import Api, ApiError

# Every tool in contracts/mcp-tools.json, by name: the Gateway has no wildcard. tests/test_roles.py fails if this
# drifts from that file (the burn-contracts package does not ship it).
FULL_ACCESS_TOOLS = ("list_tickets", "read_ticket", "read_config", "query_customers", "send_message", "read_inbox",
                     "notify")

# Commands that run something else: the role is named after the script, not the interpreter or launcher.
LAUNCHERS = {"python", "python3", "node", "bun", "deno", "uv", "uvx", "npx", "bash", "sh", "zsh"}
SUBCOMMANDS = {"run", "exec"}  # uv run, bun run, deno run, uv tool run's "run": the script comes after them
JOIN_TTL = 300  # seconds the minted join token lives; burn run redeems it at once


class RoleError(Exception):
    """No role can be named after this command, or the one named cannot be used. The message says what to do."""


def full_access(name: str) -> dict:
    """The role body for full access: every tool, the instance's own AWS key, and no egress narrowing."""
    return {"name": name, "tools": list(FULL_ACCESS_TOOLS), "credentials": ["aws_key"], "may_message": [],
            "may_spawn": [], "egress": None, "owner": None, "canaries": True, "auto_burn": True,
            "replace_on_burn": False}


def is_full_access(role: dict) -> bool:
    """Full access, as the console shows it: every tool and no egress narrowing. Anything else is Limited."""
    return set(role.get("tools") or []) == set(FULL_ACCESS_TOOLS) and role.get("egress") is None


def describe(role: dict) -> str:
    """How a reused role is described on stderr, so nobody expects full access and silently gets less."""
    if is_full_access(role):
        return "Full access"
    egress = role.get("egress")
    return (f"Limited, not Full access: tools {', '.join(role.get('tools') or []) or 'none'}; "
            f"egress {'any allowed host' if egress is None else ', '.join(egress) or 'none'}")


def _clean(word: str) -> str:
    name = re.sub(r"[^a-z0-9-]", "-", word.lower()).strip("-")[:32].strip("-")
    return name if names.is_role_name(name) else ""


def _script(args: list[str]) -> str:
    """The script an interpreter or launcher runs: `python -u agent.py` -> agent.py, `python -m pkg.agent` -> agent,
    `uv run python agent.py` -> agent.py. Empty when there is none (`python -c ...`, a bare `node`)."""
    for i, arg in enumerate(args):
        if arg == "-m" and i + 1 < len(args):
            return args[i + 1].rsplit(".", 1)[-1]
        if arg in ("-c", "-e", "--eval"):
            return ""
        if arg.startswith("-") or arg in SUBCOMMANDS:
            continue
        if PurePath(arg).name in LAUNCHERS:
            return _script(args[i + 1:])
        return arg
    return ""


def name_for(command: list[str]) -> str:
    """The role name for a command: its program's name, or for an interpreter or launcher its script's stem."""
    program = PurePath(command[0]).name if command else ""
    launcher = program in LAUNCHERS or re.fullmatch(r"python3\.\d+", program)  # python3.12 too
    word = PurePath(_script(command[1:])).stem if launcher else program
    name = _clean(word)
    shown = " ".join(command[:3])
    if not name:
        raise RoleError(f"cannot name a role after `{shown}`: pass --role")
    if names.is_reserved_role_name(name):
        raise RoleError(f"`{shown}` would make role \"{name}\", a name kept for the eval harness: pass --role")
    return name


def get_or_create(api: Api, name: str, derived: bool) -> tuple[dict, bool]:
    """The role, and whether this call created it. An existing role is used as it is. A missing one is created with
    full access only when burn run named it after the command: a --role that names no role is a mistake (a typo
    must not hand an agent every tool and an AWS key). Two runs creating it at once both end up with the one role."""
    try:
        role = api.request("GET", f"/roles/{name}")
    except ApiError as exc:
        if exc.status != 404:
            raise
        if not derived:
            raise RoleError(f"role \"{name}\" does not exist: create it in the console or with `burn apply`, "
                            "or leave out --role") from None
        try:
            return api.request("POST", "/roles", full_access(name)), True
        except ApiError as exc:
            if exc.status != 409:
                raise
            role = api.request("GET", f"/roles/{name}")  # another run created it first
    if derived and role.get("managed_by") == "scenario":
        raise RoleError(f"role \"{name}\" belongs to a scenario agent: pass --role")
    return role, False


def mint_join_token(api: Api, name: str) -> str:
    """A one-use join token for the role, for this run only. Never printed or logged."""
    return api.request("POST", f"/roles/{name}/tokens", {"uses": 1, "ttl": JOIN_TTL})["token"]
