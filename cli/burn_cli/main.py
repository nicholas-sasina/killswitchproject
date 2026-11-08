"""Command parsing and the commands. Exit codes: 0 ok, 1 API/network error, 2 usage/config."""
import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

from . import config, render, spec
from .api import Api, ApiError

CLOUDTRAIL_NOTE = "CloudTrail event history can lag 5-15 minutes behind real activity."


RUN_HELP = """Run COMMAND as one Burn instance: burn run --role worker -- python agent.py

burn run joins with a join token (--token or BURN_JOIN_TOKEN), fetches the instance's own credentials, and
starts COMMAND with only those: your AWS_* variables, BURN_JOIN_TOKEN, the API token and other credential
variables are removed. The agent gets BURN_MCP_URL (a local endpoint that adds the badge token), BURN_SPAWN_URL
(POST {role, command} to start a child instance), BURN_SESSION_ID, BURN_AGENT_ID and BURN_ROLE.
It sends heartbeats, stops the agent if the instance is burned, and reports a clean exit.

HTTPS_PROXY / HTTP_PROXY (from BURN_PROXY_URL) are a convenience so well-behaved clients route through the
egress proxy. They are not containment: a process can ignore them. Containment is the network rule in the
demo stack, which gives the agent no route except the proxy.

Who the agent runs as: when burn run is root (a container's PID 1) the agent runs as --user (default: agent,
else nobody), so it cannot read burn run's environment or files. When burn run is not root, the agent runs as
you: it can read your files and burn run's environment (/proc/<pid>/environ). Containment against a hostile
agent then needs a container or a separate user.
"""


class UsageError(Exception):
    pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="burn", description="Command-line client for the Burn API.")
    p.add_argument("--json", action="store_true", help="print the raw API response")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("agents", help="list agents and their status")
    kill = sub.add_parser("kill", help="burn an instance, a role, a lineage or a join token's instances",
                          description="Burn what you name: a SESSION (one instance), --role (every active instance), "
                          "--lineage SESSION (an instance and its descendants) or --token TOKEN_ID (every instance "
                          "that joined with it; the token is revoked too). An agent id (agent-...) burns that one agent.")
    kill.add_argument("target", nargs="?", metavar="SESSION", help="a session id (sess_...) or an agent id (agent-...)")
    scope = kill.add_mutually_exclusive_group()
    scope.add_argument("--role", help="burn every active instance of this role")
    scope.add_argument("--lineage", metavar="SESSION", help="burn this instance and all its descendants")
    scope.add_argument("--token", metavar="TOKEN_ID", help="burn every instance that joined with this token")
    kill.add_argument("--reason", default="")
    kill.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    login = sub.add_parser("login", help="store the API URL and token")
    login.add_argument("--url", help="Burn API URL (default: BURN_API_URL or a prompt)")
    login.add_argument("--token-stdin", action="store_true", help="read the token from stdin instead of prompting")
    login.add_argument("--no-verify", action="store_true", help="store without checking them against the API")
    init = sub.add_parser("init", help="write a starter burn.yaml")
    init.add_argument("-f", "--file", default=spec.DEFAULT_FILE)
    init.add_argument("--force", action="store_true", help="overwrite an existing file")
    apply = sub.add_parser("apply", help="create or update the roles in burn.yaml")
    apply.add_argument("-f", "--file", default=spec.DEFAULT_FILE)
    pull = sub.add_parser("pull", help="write roles made in the dashboard out to burn.yaml")
    pull.add_argument("-f", "--file", default=spec.DEFAULT_FILE)
    token = sub.add_parser("token", help="join tokens").add_subparsers(dest="token_command", required=True)
    create = token.add_parser("create", help="create a join token for a role (shown once)")
    create.add_argument("role")
    create.add_argument("--uses", type=int, default=1, help="how many instances may join (default 1)")
    create.add_argument("--ttl", type=int, default=3600, help="seconds until it expires (default 3600)")
    run_p = sub.add_parser("run", help="run a command as a Burn instance", description=RUN_HELP,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
    run_p.add_argument("--role", required=True)
    run_p.add_argument("--token", help="join token (prefer the BURN_JOIN_TOKEN environment variable: "
                                       "command lines are visible to other users)")
    run_p.add_argument("--user", help="run the agent as this user when burn run is root "
                                      "(default: agent if it exists, else nobody)")
    run_p.add_argument("--adopt", help=argparse.SUPPRESS)  # a child whose session its parent already started
    run_p.add_argument("cmd", nargs=argparse.REMAINDER, metavar="-- COMMAND")
    env_p = sub.add_parser("env", help="show the environment `burn run` gives an instance")
    env_p.add_argument("--show", action="store_true", help="show the values of the kept variables")
    ps = sub.add_parser("ps", help="list instances grouped by role, with lineage")
    ps.add_argument("--role")
    ps.add_argument("--status", choices=["active", "revoked", "exited"])
    sub.add_parser("ledger", help="credentials an agent has seen").add_argument("agent_id")
    sub.add_parser("blast-radius", help="CloudTrail activity of an agent's AWS keys").add_argument("agent_id")
    ev = sub.add_parser("events", help="show the event stream")
    ev.add_argument("--follow", action="store_true", help="keep polling every second (Ctrl+C to stop)")
    ev.add_argument("--agent", help="only events of this agent")
    ev.add_argument("--since", help="only events after this event_id")
    ev.add_argument("--max-polls", type=int, help=argparse.SUPPRESS)  # lets tests end --follow
    return p


def _emit(args, data, text: str) -> None:
    print(json.dumps(data, indent=2) if args.json else text)


def cmd_agents(api, args):
    agents = api.request("GET", "/events?limit=1")["agents"]
    _emit(args, agents, render.table(["agent", "role", "status", "session", "exposures"],
          [[a["agent_id"], a.get("role"), a["status"], a.get("session_id"), a.get("exposures")] for a in agents]))


def _burned_lines(r: dict) -> list[str]:
    return [f"burned {r['agent_id']}: revoked={str(r['revoked']).lower()}",
            f"sessions revoked: {', '.join(r['sessions_revoked']) or '-'}",
            f"rotated: {', '.join(r['rotated']) or '-'}",
            f"flagged: {', '.join(r['flagged']) or '-'}"]


def _kill_target(args) -> tuple[str, str] | None:
    """(scope, id) for the scoped burn the arguments name, or None for a plain agent id."""
    if args.role:
        return "role", args.role
    if args.lineage:
        return "lineage", args.lineage
    if args.token:
        return "token", args.token
    if args.target and not args.target.startswith("agent-"):
        return "instance", args.target
    return None


def cmd_kill(api, args):
    scoped = _kill_target(args)
    what = f"{scoped[0]} {scoped[1]}" if scoped else args.target
    if not args.yes:
        answer = input(f"Burn {what}? This revokes badges and rotates credentials. [y/N] ")
        if answer.strip().lower() != "y":
            print("aborted")
            return 1
    reason = {"reason": args.reason} if args.reason else {}
    if scoped is None:
        r = api.request("POST", "/burn", {"agent_id": args.target, **reason})
        return _emit(args, r, "\n".join(_burned_lines(r)))
    r = api.request("POST", "/burns", {"scope": scoped[0], "id": scoped[1], **reason})
    lines = [f"burned {len(r['burned'])} instance(s) by {r['scope']} {r['id']}"]
    for burned in r["burned"]:
        lines += ["", *_burned_lines(burned)]
    lines += [f"failed: {f.get('agent_id')}: {f.get('error')}" for f in r.get("failed", [])]
    _emit(args, r, "\n".join(lines))
    return 1 if r.get("failed") else 0


def cmd_ledger(api, args):
    r = api.request("GET", f"/agents/{args.agent_id}/ledger")
    rows = [[e.get("secret_ref") or "(unknown format)", e["fingerprint"][:8], e["secret_type"], e["owner"],
             e["source"], e.get("via"), e["status"]] for e in r["entries"]]
    _emit(args, r, render.table(["secret", "fp", "type", "owner", "source", "via", "status"], rows)
          if rows else f"{args.agent_id} has not seen any credentials.")


def cmd_blast_radius(api, args):
    r = api.request("GET", f"/burn/{args.agent_id}/blast-radius")
    parts = []
    if r["keys"]:
        parts.append(render.table(["key", "hint", "status"], [[k["key_ref"], k.get("key_hint"), k.get("status")] for k in r["keys"]]))
    if r["events"]:
        parts.append(render.table(["time", "key", "event", "source", "resource", "error"],
                     [[e["ts"], e["key_ref"], e["event_name"], e.get("event_source"), e.get("resource"), e.get("error_code")]
                      for e in r["events"]]))
    else:
        parts.append("No CloudTrail activity recorded yet. " + r.get("note", CLOUDTRAIL_NOTE))
    _emit(args, r, "\n\n".join(parts))


def cmd_events(api, args):
    cursor, polls = args.since, 0
    while True:
        query = f"?since={cursor}" if cursor else ""
        r = api.request("GET", "/events" + query)
        cursor = r.get("cursor") or cursor
        events = [e for e in r["events"] if not args.agent or e["agent_id"] == args.agent]
        for e in events:
            print(json.dumps(e) if args.json else render.event_line(e), flush=True)
        polls += 1
        if not args.follow or (args.max_polls and polls >= args.max_polls):
            return 0
        time.sleep(1)


def cmd_login(args, dotenv):
    url = (args.url or config.get("BURN_API_URL", dotenv) or input("Burn API URL: ")).strip().rstrip("/")
    token = (sys.stdin.readline() if args.token_stdin else getpass.getpass("Demo token (hidden): ")).strip()
    if not url or not token:
        raise UsageError("both a URL and a token are required")
    if not args.no_verify:
        Api(url, token).request("GET", "/roles")  # a wrong URL or token stops here, before anything is stored
    path = config.save(url, token, dotenv)
    print(f"logged in to {url}; settings saved to {path}")


def cmd_init(args):
    path = Path(args.file)
    if path.exists() and not args.force:
        raise UsageError(f"{path} already exists (use --force to overwrite)")
    path.write_text(spec.TEMPLATE)
    print(f"wrote {path}. Edit it, then run `burn apply`.")


def cmd_apply(api, args):
    doc = spec.load(Path(args.file))  # validated before any call, so a typo changes nothing
    saved = [api.request("PUT", f"/roles/{role['name']}", spec.role_body(role)) for role in doc["roles"]]
    _emit(args, saved, "\n".join(f"applied role {r['name']} (managed by code)" for r in saved))


def cmd_pull(api, args):
    roles = [r for r in api.request("GET", "/roles")["roles"] if r["managed_by"] == "ui"]
    if not roles:
        print("no dashboard-made roles to pull")
        return 0
    path = Path(args.file)
    path.write_text(spec.dump(spec.from_api(roles)))
    print(f"wrote {len(roles)} role(s) to {path}: " + ", ".join(r["name"] for r in roles))
    print("run `burn apply` to manage them from code; the dashboard then shows them read-only")


def cmd_token_create(api, args):
    r = api.request("POST", f"/roles/{args.role}/tokens", {"uses": args.uses, "ttl": args.ttl})
    # The only place a join token is ever printed. The API will not show it again.
    if args.json:
        print(json.dumps(r, indent=2))
        return
    print(f"token {r['token_id']} for role {r['role']}: {r['uses']} use(s), expires {r['expires_at']}")
    print("Shown once; it cannot be read again.\n")
    print(r["token"])
    print(f"\nJoin with:\n  BURN_JOIN_TOKEN={r['token']} burn run --role {r['role']} -- <command>")


def cmd_run(api, args):
    from .runner import credentials, privs
    from .runner.run import Instance
    command = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if not command:
        raise UsageError("give the command to run after --, e.g. burn run --role worker -- python agent.py")
    token = args.token or os.environ.get("BURN_JOIN_TOKEN", "")
    session = json.loads(args.adopt) if args.adopt else None
    if not token and not session:
        raise UsageError("a join token is required: set BURN_JOIN_TOKEN or pass --token")
    gateway = config.get("BURN_GATEWAY_URL", args.dotenv)
    if not gateway:
        raise UsageError("BURN_GATEWAY_URL not set (run `make env`, or export it)")
    try:
        source = credentials.from_name(os.environ.get("BURN_CREDENTIAL_SOURCE", ""))
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    try:
        drop = privs.resolve(args.user)
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    return Instance(api, source, args.role, command, gateway_url=gateway.rstrip("/"), enroll_token=token,
                    session=session, proxy_url=config.get("BURN_PROXY_URL", args.dotenv), drop=drop, user_arg=args.user).run()


def cmd_env(args):
    from .runner import env
    print("\n".join(env.describe(dict(os.environ), config.get("BURN_PROXY_URL", args.dotenv), args.show)))


def cmd_ps(api, args):
    query = "&".join(f"{k}={v}" for k, v in (("role", args.role), ("status", args.status)) if v)
    instances = api.request("GET", "/instances" + (f"?{query}" if query else ""))["instances"]
    if args.json:
        return print(json.dumps(instances, indent=2))
    if not instances:
        return print("no instances")
    by_id = {i["session_id"]: i for i in instances}

    def depth(i):
        return 0 if i.get("parent_session") not in by_id else 1 + depth(by_id[i["parent_session"]])

    blocks = []
    for role in sorted({i["role"] for i in instances}):
        rows = [[("  " * depth(i)) + i["session_id"], i["agent_id"], i["status"], i.get("parent_session"),
                 i.get("token_id"), i.get("last_seen")] for i in instances if i["role"] == role]
        blocks.append(f"{role} ({len(rows)})\n" + render.table(
            ["session", "agent", "status", "parent", "token", "last seen"], rows))
    print("\n\n".join(blocks))


COMMANDS = {"agents": cmd_agents, "run": cmd_run, "apply": cmd_apply, "pull": cmd_pull, "ps": cmd_ps, "token": cmd_token_create, "kill": cmd_kill, "ledger": cmd_ledger,
            "blast-radius": cmd_blast_radius, "events": cmd_events}


def run(argv: list[str] | None = None, dotenv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)  # argparse exits 2 on bad usage
    args.dotenv = dotenv
    if args.command == "kill" and not (args.target or args.role or args.lineage or args.token):
        parser.error("name what to burn: a SESSION, an agent id, --role, --lineage or --token")
    try:
        if args.command in ("login", "init", "env"):  # these need no API config
            {"login": lambda: cmd_login(args, dotenv), "init": lambda: cmd_init(args), "env": lambda: cmd_env(args)}[args.command]()
            return 0
        url, token = config.load(dotenv)
        return COMMANDS[args.command](Api(url, token), args) or 0
    except (config.ConfigError, UsageError, spec.SpecError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
