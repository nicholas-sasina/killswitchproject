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


def _plain(html_text: str) -> str:
    """Telegram returns message text without the HTML markup it rendered."""
    import html as html_mod
    import re
    return html_mod.unescape(re.sub(r"<[^>]+>", "", html_text))


class _RecordingTransport:
    """Pretends every provider accepted the message. Never opens a socket."""

    def __init__(self, provider: str):
        self.provider = provider

    def post(self, url, body, headers, *, timeout):
        sent = json.loads(body)  # answer like the real APIs: they return the message they stored
        if self.provider == "telegram":
            return Response(200, {}, json.dumps({"ok": True, "result": {"message_id": 1, "text": _plain(
                sent.get("text", ""))}}).encode())
        if self.provider == "discord":
            return Response(200, {}, json.dumps({"id": "1", "embeds": sent.get("embeds", [])}).encode())
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
    if not _apply_env_file(args, out):
        return 2
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


REPO_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))


class EnvFileError(Exception):
    pass


def load_env_file(path: str) -> list[str]:
    """Load KEY=VALUE lines (optional `export `) into os.environ. Returns the key names only.

    Refuses a file inside the repository or readable by group/others (chmod 600), so secrets are
    never committed or shared by accident. Values are never printed.
    """
    real = os.path.realpath(os.path.expanduser(path))
    if real == REPO_ROOT or real.startswith(REPO_ROOT + os.sep):
        raise EnvFileError("refusing an env file inside the repository; keep it in ~/.config/burn-notify/")
    st = os.stat(real)
    if st.st_mode & 0o077:
        raise EnvFileError(f"refusing {path}: readable by others (run: chmod 600 {path})")
    keys = []
    with open(real, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, sep, value = line.partition("=")
            if not sep or not key.replace("_", "").isalnum():
                raise EnvFileError("env file lines must look like KEY=VALUE")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ[key] = value
            keys.append(key)
    return keys


def _apply_env_file(args, out) -> bool:
    if getattr(args, "env_file", None):
        try:
            keys = load_env_file(args.env_file)
        except (EnvFileError, OSError) as exc:
            out.write(f"{exc}\n")
            return False
        out.write(f"loaded {len(keys)} variable(s) from the env file: {', '.join(sorted(keys))}\n")
    return True


def cmd_live_check(args, out=sys.stdout) -> int:
    from . import livecheck
    if not _apply_env_file(args, out):
        return 2
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
    ok = report.get("duplicate_sent_nothing") and report.get("redaction_leaks") == [] and all(
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
    send.add_argument("--env-file", help="chmod-600 KEY=VALUE file outside the repo holding the secrets")
    live = sub.add_parser("live-check", help="step-by-step verification against authorized test destinations")
    live.add_argument("--config", help="config JSON naming ONLY authorized test destinations")
    live.add_argument("--live", action="store_true")
    live.add_argument("--dry-run", action="store_true", help="same sequence with recording transports, no network")
    live.add_argument("--inject-failure", action="store_true", help="also test retry after controlled failures")
    live.add_argument("--report", help="write the JSON report (no secrets) to this path")
    live.add_argument("--env-file", help="chmod-600 KEY=VALUE file outside the repo holding the secrets")
    msg = sub.add_parser("test-message", help="print an SQS body that checks a deployed stack ([TEST] label)")
    msg.add_argument("--fixture", default="canary_used", choices=sorted(fixtures.ALL))
    args = parser.parse_args(argv)
    return {"dry-run": cmd_dry_run, "send": cmd_send, "live-check": cmd_live_check,
            "test-message": cmd_test_message}[args.cmd](args, out)
