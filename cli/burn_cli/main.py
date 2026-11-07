"""Command parsing and the six commands. Exit codes: 0 ok, 1 API/network error, 2 usage/config."""
import argparse
import json
import sys
import time

from . import config, render
from .api import Api, ApiError

CLOUDTRAIL_NOTE = "CloudTrail event history can lag 5-15 minutes behind real activity."


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="burn", description="Command-line client for the Burn API.")
    p.add_argument("--json", action="store_true", help="print the raw API response")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("agents", help="list agents and their status")
    kill = sub.add_parser("kill", help="burn an agent: revoke its badge and rotate its credentials")
    kill.add_argument("agent_id")
    kill.add_argument("--reason", default="")
    kill.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
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


def cmd_kill(api, args):
    if not args.yes:
        answer = input(f"Burn {args.agent_id}? This revokes its badge and rotates its credentials. [y/N] ")
        if answer.strip().lower() != "y":
            print("aborted")
            return 1
    body = {"agent_id": args.agent_id, **({"reason": args.reason} if args.reason else {})}
    r = api.request("POST", "/burn", body)
    lines = [f"burned {r['agent_id']}: revoked={str(r['revoked']).lower()}",
             f"sessions revoked: {', '.join(r['sessions_revoked']) or '-'}",
             f"rotated: {', '.join(r['rotated']) or '-'}",
             f"flagged: {', '.join(r['flagged']) or '-'}"]
    _emit(args, r, "\n".join(lines))


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


COMMANDS = {"agents": cmd_agents, "kill": cmd_kill, "ledger": cmd_ledger,
            "blast-radius": cmd_blast_radius, "events": cmd_events}


def run(argv: list[str] | None = None, dotenv=None) -> int:
    args = build_parser().parse_args(argv)  # argparse exits 2 on bad usage
    try:
        url, token = config.load(dotenv)
    except config.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        return COMMANDS[args.command](Api(url, token), args) or 0
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
