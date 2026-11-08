"""Reproducible demo: Burn's own containment code produces the events, notifications deliver them.

    cd notifications
    uv run python demo/backend_demo.py                       # dry run: nothing leaves the machine
    uv run python demo/backend_demo.py --scenario rotation-failure
    BURN_NOTIFY_LIVE=1 uv run python demo/backend_demo.py --live --config test-config.json

What is real and what is not:
  * REAL CODE: the mock provider, the Burn API canary containment and the minter run unmodified,
    through burn_api/scripts/canary_harness.py (the same harness Burn's own tests use). Every event
    below was written to burn-events by that code; nothing is hand-made.
  * EMULATED AWS: DynamoDB, Secrets Manager and IAM are moto, in this process. No AWS account is used.
    The "leak" and the attacker's presentation of the canary are simulated by the harness.
  * The events are wrapped as DynamoDB Stream records (what the burn-events stream delivers) and fed
    to the notification processor. Every message is labelled [TEST] / SYNTHETIC.

Timeline shown: incident detected -> alert delivered -> session revoked -> containment update
-> final credential summary.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "notifications"))
sys.path.insert(0, str(ROOT / "burn_api" / "scripts"))

NOTICE = ("SYNTHETIC TEST - produced by Burn's containment code on emulated AWS (local demo); "
          "not a real security event")


def burn_events(scenario: str, workers: int) -> tuple[list[dict], dict]:
    """Run Burn's real canary path on moto and return the ledger rows it wrote, oldest first."""
    import canary_harness as harness
    from moto import mock_aws
    os.environ.update(harness.ENV)
    with mock_aws():
        stack = harness.Stack(workers=workers)
        stack.wire(setattr)
        if scenario == "rotation-failure":
            import minter_client
            real = stack.minter_call

            def failing(method, path, body=None, query=None):
                if path.endswith("/rotate"):
                    raise minter_client.MinterError("minter POST rotate failed: 500")
                return real(method, path, body, query)
            minter_client.call = failing
        sessions = {a: stack.start_session(a) for a in stack.agents}
        victim = sorted(sessions)[len(sessions) // 3]
        mark = {e["event_id"] for e in stack.events()}
        stolen = harness.attacker_decode(harness.leak_gzip_base64(stack.read_canary(sessions[victim])))
        provider_status, _ = stack.present(stolen)
        events = [{k: v for k, v in e.items() if k != "stream"} for e in stack.events() if e["event_id"] not in mark]
        outcome = stack.incidents[-1] if stack.incidents else {}
        others = [stack.session_status(s["session_id"]) for a, s in sessions.items() if a != victim]
        facts = {"victim": victim, "provider_status": provider_status, "outcome": outcome.get("outcome"),
                 "victim_session": stack.session_status(sessions[victim]["session_id"]),
                 "other_sessions_active": f"{others.count('active')}/{len(others)}"}
    return events, facts


def build_processor(args):
    from burn_notify import cli, config as config_mod
    from burn_notify.processor import Processor
    from burn_notify.store import MemoryStore

    class Clock:
        offset = 0.0

        def __call__(self):
            return time.time() + self.offset

    clock = Clock()
    if args.live:
        if os.environ.get("BURN_NOTIFY_LIVE") != "1" or not args.config:
            sys.exit("refusing to send: --live needs --config and BURN_NOTIFY_LIVE=1 (authorized test channels only)")
        cfg = config_mod.load({**os.environ, "BURN_NOTIFY_CONFIG": args.config})
        proc = Processor(cfg, MemoryStore(), clock=clock, notice=NOTICE)
    else:
        cfg = config_mod.load({**os.environ, "BURN_NOTIFY_CONFIG": args.config}) if args.config \
            else config_mod.parse(cli.DEMO_CONFIG, environ={})
        proc = cli.dry_run_processor(cfg, clock)
        proc.notice = NOTICE
    return proc, clock


STAGE = {"canary_used": "incident detected", "revoked": "session revoked", "rotated": "credential rotated",
         "flagged": "flagged / containment result"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", choices=["contained", "rotation-failure"], default="contained")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--config", help="notifications config (default: built-in demo config, dry run)")
    parser.add_argument("--live", action="store_true", help="deliver to the config's AUTHORIZED test destinations")
    parser.add_argument("--json", action="store_true", help="print the full machine-readable record")
    parser.add_argument("--env-file", help="chmod-600 KEY=VALUE file outside the repo holding the secrets")
    args = parser.parse_args(argv)
    if args.env_file:
        from burn_notify.cli import load_env_file
        keys = load_env_file(args.env_file)
        print(f"loaded {len(keys)} variable(s) from the env file: {', '.join(sorted(keys))}")

    from burn_notify import fixtures
    events, facts = burn_events(args.scenario, args.workers)
    print("== Burn (real code, emulated AWS):", json.dumps(facts))
    proc, clock = build_processor(args)
    print(f"== delivery mode: {'LIVE to configured test destinations' if args.live else 'dry run (no network)'}\n")
    record = []
    for event in events:
        result = proc.process_record(fixtures.stream_record(event))  # exactly what the stream would carry
        sent = [o.destination_id for o in result.outcomes if o.status == "delivered"]
        other = [f"{o.destination_id}:{o.status}" for o in result.outcomes if o.status != "delivered"]
        stage = STAGE.get(event["action"], event["action"])
        print(f"{event['ts']}  {event['event_id']}  {event['source']}/{event['action']:<12} -> {stage}")
        print(f"   {result.severity or '-':<8} {result.category or '-':<24} delivered to: {', '.join(sent) or 'none'}"
              + (f"   ({', '.join(other)})" if other else ""))
        record.append({"event": {k: event[k] for k in ("event_id", "ts", "agent_id", "source", "action",
                                                       "session_id", "flag_level")},
                       "category": result.category, "severity": result.severity,
                       "outcomes": [o.__dict__ for o in result.outcomes]})
    clock.offset += 120  # the scheduled flush runs; the summary is due 45 s after the update
    flushed = proc.flush_windows()
    if proc.flushed:
        dest, inc = proc.flushed[0]
        print(f"\n(+~1 min) final credential summary -> {len(flushed)} destination(s), "
              f"{sum(o.status == 'delivered' for o in flushed)} delivered")
        for label, value in inc.fields():
            if label in ("Status", "Owned credentials", "Shared credentials", "Other instances", "Severity"):
                print(f"   {label}: {value}")
    duplicate = proc.process_record(fixtures.stream_record(events[0]))
    print(f"\nduplicate stream record for {events[0]['event_id']}: "
          f"{sorted({o.status for o in duplicate.outcomes}) or ['not routed']}")
    if args.json:
        print(json.dumps({"burn": facts, "events": record, "deliveries": proc.store.list_deliveries()},
                         indent=2, default=str))
    print("\nEvents were written by Burn's real code on emulated AWS; messages are labelled [TEST].")
    return 0


if __name__ == "__main__":
    sys.exit(main())
