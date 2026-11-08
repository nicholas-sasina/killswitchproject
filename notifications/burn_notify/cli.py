"""Command line: dry run (no network) and an opt-in live test send.

  python -m burn_notify dry-run --demo
      canary alert, then the containment update, through all five providers; prints
      redacted previews and the delivery table. Nothing leaves the machine.
  python -m burn_notify dry-run --fixture blocked --fixture flagged_reported [--config FILE]
  python -m burn_notify send --config FILE --destination ID --fixture canary_used --live
      sends ONE synthetic alert to ONE configured destination. Requires BURN_NOTIFY_LIVE=1
      as well as --live, and is meant for authorized test channels only.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
import os
import sys
import time

from . import config as config_mod
from . import fixtures
from .http import Response
from .processor import Processor
from .providers import REGISTRY
from .redact import redact
from .store import MemoryStore

DEMO_CONFIG = {
    "dashboard_origin": "https://burn.example.com",
    "destinations": [
        {"id": "slack-security", "provider": "slack", "min_severity": "medium", "secret_ref": "env:DEMO_SLACK",
         "options": {"private_channel_confirmed": True}},
        {"id": "telegram-oncall", "provider": "telegram", "min_severity": "high", "recipient": "-1001234567890",
         "secret_ref": "env:DEMO_TELEGRAM", "options": {"allowed_chat_ids": ["-1001234567890"]}},
        {"id": "discord-security", "provider": "discord", "min_severity": "medium", "secret_ref": "env:DEMO_DISCORD",
         "options": {"private_channel_confirmed": True}},
        {"id": "teams-secops", "provider": "teams", "min_severity": "high", "secret_ref": "env:DEMO_TEAMS"},
        {"id": "email-secops", "provider": "email", "min_severity": "high", "recipient": "secops@example.com",
         "options": {"from_address": "burn-alerts@example.com"}},
    ],
}


class _RecordingTransport:
    """Pretends every provider accepted the message. Never opens a socket."""

    def __init__(self, provider: str):
        self.provider = provider

    def post(self, url, body, headers, *, timeout):
        if self.provider == "telegram":
            return Response(200, {}, b'{"ok":true,"result":{"message_id":1}}')
        if self.provider == "discord":
            return Response(200, {}, b'{"id":"1"}')
        if self.provider == "teams":
            return Response(202, {}, b"")
        return Response(200, {}, b"ok")


class _FakeSES:
    def send_email(self, **kwargs):
        return {"MessageId": "dry-run"}


class _DummySecrets:
    """Dry run: fake secret values with the right shape, never real ones."""

    def get(self, ref):
        if ref and "TELEGRAM" in ref:
            return "123456:" + "x" * 35
        return "https://dry-run.invalid/"


class _Clock:
    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.time() + self.offset


def dry_run_processor(cfg, clock=time.time) -> Processor:
    providers = {name: cls(transport=_RecordingTransport(name)) for name, cls in REGISTRY.items() if name != "email"}
    providers["email"] = REGISTRY["email"](ses_client=_FakeSES())
    return Processor(cfg, MemoryStore(), providers=providers, secrets=_DummySecrets(), clock=clock,
                     notice="SYNTHETIC TEST - dry run, nothing was sent")


def _print_preview(proc: Processor, event: dict, out) -> None:
    inc, superseded = proc.incident_for(event)
    out.write(f"\n=== {event['action']} ({event['source']}) -> {inc.header}: {inc.title}\n")
    out.write(f"    category={inc.category} severity={inc.severity.label} superseded={superseded}\n")
    for dest in proc.config.destinations:
        decision = config_mod.route(dest, inc)
        if decision is None:
            out.write(f"--- {dest.id} ({dest.provider}): not routed\n")
            continue
        prepared = proc.providers[dest.provider].build(inc, dest)
        out.write(f"--- {dest.id} ({dest.provider}): {decision}\n")
        out.write(redact(json.dumps(prepared.preview, indent=2, ensure_ascii=False)) + "\n")


def cmd_dry_run(args, out=sys.stdout) -> int:
    cfg = config_mod.load() if args.config is None and os.environ.get("BURN_NOTIFY_CONFIG") else None
    if args.config:
        cfg = config_mod.load({**os.environ, "BURN_NOTIFY_CONFIG": args.config})
    cfg = cfg or config_mod.parse(DEMO_CONFIG, environ={})
    clock = _Clock()
    proc = dry_run_processor(cfg, clock)
    names = ["canary_used", "canary_revoked", "rotated", "flagged_seen"] if args.demo else (args.fixture or ["canary_used"])
    events = []
    for i, name in enumerate(names):
        if name not in fixtures.ALL:
            out.write(f"unknown fixture {name}; choose from {sorted(fixtures.ALL)}\n")
            return 2
        events.append(fixtures.ALL[name](offset_ms=i))
    for event in events:
        if args.quiet:
            proc.process_event(event)
            continue
        preview_proc = dry_run_processor(cfg)  # previews must not consume the real run's state
        preview_proc.store = proc.store
        _print_preview(preview_proc, event, out)
        proc.process_event(event)
    clock.offset += 120  # let aggregation windows and containment summaries come due
    flushed = proc.flush_windows()
    if flushed and not args.quiet:
        out.write(f"\n=== scheduled flush (simulated +120s): {len(flushed)} summary message(s)\n")
        shown = set()
        for dest_id, inc in proc.flushed:
            if inc.incident_id in shown:
                continue
            shown.add(inc.incident_id)
            out.write(f"--- first sent to {dest_id}: {inc.header}: {inc.title}\n")
            out.write("".join(f"    {label}: {value}\n" for label, value in inc.fields()))
    out.write("\n=== delivery history (local store)\n")
    for row in proc.store.list_deliveries():
        out.write(f"{row['event_id']}  {row['destination_id']:<18} {row['provider']:<9} {row['status']:<19}"
                  f" attempts={row['attempts']} {row.get('failure_category') or ''}\n")
    out.write("\nDry run: no message left this machine.\n")
    return 0


def cmd_send(args, out=sys.stdout) -> int:
    if not args.live or os.environ.get("BURN_NOTIFY_LIVE") != "1":
        out.write("refusing to send: pass --live and set BURN_NOTIFY_LIVE=1 (authorized test channels only)\n")
        return 2
    cfg = config_mod.load({**os.environ, "BURN_NOTIFY_CONFIG": args.config})
    dest = cfg.destination(args.destination)
    test_dest = replace(dest, min_severity="info")  # a test send always routes
    single = replace(cfg, destinations=(test_dest,))
    from .livecheck import NOTICE
    proc = Processor(single, MemoryStore(), notice=NOTICE)
    event = fixtures.ALL[args.fixture]()
    result = proc.process_event(event)
    for outcome in result.outcomes:
        out.write(f"{outcome.destination_id}: {outcome.status} {outcome.category or ''}\n")
    return 0 if all(o.status == "delivered" for o in result.outcomes) else 1


def cmd_live_check(args, out=sys.stdout) -> int:
    from . import livecheck
    if args.dry_run:
        cfg = config_mod.load({**os.environ, "BURN_NOTIFY_CONFIG": args.config}) if args.config \
            else config_mod.parse(DEMO_CONFIG, environ={})
        proc = dry_run_processor(cfg)
        report = livecheck.run(cfg, inject_failure=args.inject_failure, secrets=_DummySecrets(),
                               providers=proc.providers)
    else:
        if not args.live or os.environ.get("BURN_NOTIFY_LIVE") != "1":
            out.write("refusing to send: pass --live and set BURN_NOTIFY_LIVE=1 (authorized test channels only)\n")
            return 2
        if not args.config:
            out.write("--config is required for a live check\n")
            return 2
        cfg = config_mod.load({**os.environ, "BURN_NOTIFY_CONFIG": args.config})
        report = livecheck.run(cfg, inject_failure=args.inject_failure)
    out.write(livecheck.render(report) + "\n")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        out.write(f"report written to {args.report}\n")
    ok = report.get("duplicate_sent_nothing") and all(
        r["status"] in ("delivered", "suppressed") or r["status"].startswith("skipped_") or
        (s["step"].startswith("6a") and r["status"] == "retrying")
        for s in report["steps"] for r in s["results"])
    checks_ok = all(r.get("payload_check") == "pass" for s in report["steps"] for r in s["results"])
    out.write(f"overall: {'PASS' if ok and checks_ok else 'FAIL'}"
              f"{' (dry run: no message left this machine)' if args.dry_run else ''}\n")
    return 0 if ok and checks_ok else 1


def cmd_test_message(args, out=sys.stdout) -> int:
    """Print an SQS message body for checking a deployment: always delivered labelled [TEST]."""
    from .events import TEST_ENVELOPE_KEY
    out.write(json.dumps({TEST_ENVELOPE_KEY: True, "event": fixtures.ALL[args.fixture]()}) + "\n")
    return 0


def main(argv=None, out=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog="burn_notify")
    sub = parser.add_subparsers(dest="cmd", required=True)
    dry = sub.add_parser("dry-run", help="process synthetic events without contacting any platform")
    dry.add_argument("--fixture", action="append", help=f"one of {sorted(fixtures.ALL)}")
    dry.add_argument("--demo", action="store_true", help="canary alert followed by its containment update")
    dry.add_argument("--config", help="config JSON (default: built-in demo config)")
    dry.add_argument("--quiet", action="store_true", help="only print the delivery table")
    send = sub.add_parser("send", help="opt-in live send of one synthetic alert to one destination")
    send.add_argument("--config", required=True)
    send.add_argument("--destination", required=True)
    send.add_argument("--fixture", default="canary_used", choices=sorted(fixtures.ALL))
    send.add_argument("--live", action="store_true")
    live = sub.add_parser("live-check", help="step-by-step verification against authorized test destinations")
    live.add_argument("--config", help="config JSON naming ONLY authorized test destinations")
    live.add_argument("--live", action="store_true")
    live.add_argument("--dry-run", action="store_true", help="same sequence with recording transports, no network")
    live.add_argument("--inject-failure", action="store_true", help="also test retry after controlled failures")
    live.add_argument("--report", help="write the JSON report (no secrets) to this path")
    msg = sub.add_parser("test-message", help="print an SQS body that checks a deployed stack ([TEST] label)")
    msg.add_argument("--fixture", default="canary_used", choices=sorted(fixtures.ALL))
    args = parser.parse_args(argv)
    return {"dry-run": cmd_dry_run, "send": cmd_send, "live-check": cmd_live_check,
            "test-message": cmd_test_message}[args.cmd](args, out)
