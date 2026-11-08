"""Process one ledger event: classify, route, claim, deliver, record.

Each destination is handled independently: one provider's outage, bad credential or
crash never stops delivery to the others, and never touches Burn itself (this
service only reads the event stream and writes its own state table).
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, replace

from . import incident as incident_mod
from . import metrics, store as st
from .config import DIGEST, Config, route
from .events import event_time_ms, is_canary_ref, is_test_envelope, parse
from .incident import Incident
from .policy import CONTAINMENT_UPDATE, Severity, apply_overrides, classify
from .providers import REGISTRY, DeliveryError, Destination, Provider
from .providers.base import backoff, sleep_for
from .redact import get_logger
from .secrets import SecretError, SecretResolver

log = get_logger()
QUICK_RETRY_MAX_WAIT = 2.0
DEPLOYMENT_TEST_NOTICE = "SYNTHETIC TEST - deployment check message; not a real security event"
SUMMARY_DELAY = 45  # seconds after a containment update before its summary is due
SUMMARY_HORIZON_MS = 10 * 60 * 1000  # containment results counted: from the revoke to 10 minutes after it
DEST_BUDGET = 15.0  # seconds of Lambda time a destination may need (connect + read + one quick retry)


@dataclass
class Outcome:
    destination_id: str
    status: str
    category: str | None = None
    retry_after: float | None = None
    echo: str | None = field(default=None, repr=False)  # provider's stored copy (live check only)


@dataclass
class EventResult:
    event_id: str | None
    category: str | None = None
    severity: str | None = None
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def needs_retry(self) -> bool:
        return any(o.status == st.RETRYING for o in self.outcomes)

    @property
    def exhausted(self) -> bool:
        """Some destination ran out of retries: the message should go to the DLQ for inspection."""
        return any(o.status == st.FAILED and (o.category or "").startswith("retries_exhausted")
                   for o in self.outcomes)

    @property
    def retry_after(self) -> float | None:
        waits = [o.retry_after for o in self.outcomes if o.status == st.RETRYING and o.retry_after is not None]
        return max(waits) if waits else None


def _fact_kind(event: dict, category: str) -> str | None:
    if event["agent_id"] == "system":
        return None
    return {"containment_failed": "rotation_failed", "containment_partial": "rotation_deferred",
            "agent_burned": "revoked", "canary_contained": "revoked", "credential_rotated": "rotated",
            "flag_seen": "flagged", "flag_attempted": "flagged", "flag_reported": "flagged",
            "flag_review": "flagged"}.get(category)


def _since(facts: st.Facts, start_ms: int | None) -> st.Facts:
    """Only results recorded at or after the revoke (and within the horizon): earlier incidents for the
    same agent, or rotations from before this containment, must not be counted as its outcome."""
    if start_ms is None:
        return facts

    def keep(ids):
        out = set()
        for event_id in ids:
            ms = event_time_ms(event_id)
            if ms is not None and start_ms <= ms <= start_ms + SUMMARY_HORIZON_MS:
                out.add(event_id)
        return out
    return st.Facts(**{kind: keep(getattr(facts, kind)) for kind in st.FACT_KINDS})


def _containment_facts(facts: st.Facts, *, final: bool = False) -> list[tuple[str, str]]:
    if facts.rotation_failed:
        owned = "NOT rotated (rotation failed) - action required"
    elif facts.rotation_deferred:
        owned = "Not rotated (deferred) - review required"
    elif facts.rotated:
        owned = f"Rotated where confirmed ({len(facts.rotated)} rotation event(s) so far)"
    elif final:
        owned = "No rotation recorded since the revoke - verify in the dashboard"
    else:
        owned = "Rotation not yet confirmed"
    shared = (f"Flagged for review ({len(facts.flagged)} flag event(s) so far)" if facts.flagged
              else "None flagged since the revoke" if final else "None flagged so far")
    return [("Owned credentials", owned), ("Shared credentials", shared)]


class Processor:
    def __init__(self, config: Config, store: st.Store, *, providers: dict[str, Provider] | None = None,
                 secrets: SecretResolver | None = None, clock=time.time, max_receives: int = 5,
                 notice: str | None = None, monotonic=time.monotonic):
        self.config = config
        self.store = store
        self.providers = providers if providers is not None else {name: cls() for name, cls in REGISTRY.items()}
        self.secrets = secrets or SecretResolver(allow_env=config.allow_env_secrets)
        self.clock = clock
        self.max_receives = max_receives
        self.notice = notice  # set only for synthetic test sends; labels every message [TEST]
        self.monotonic = monotonic
        self.flushed: list[tuple[str, Incident]] = []  # summaries sent by flush_windows (for the dry run)

    # -- incident ------------------------------------------------------------------------
    def incident_for(self, event: dict, notice: str | None = None) -> tuple[Incident, bool]:
        """Build the incident and say whether it is superseded (a stale, lower flag level)."""
        now = self.clock()
        result = apply_overrides(classify(event), self.config.severity_overrides)
        superseded = False
        kind = _fact_kind(event, result.category)
        facts = self.store.add_fact(event["agent_id"], kind, event["event_id"], now) if kind else None
        # Flag-level ordering is shared state across events: a deployment test message must never
        # raise it (it could suppress a later real alert for a credential with the same name).
        if event["action"] == "flagged" and event.get("flag_level") and event.get("secret_ref") \
                and not is_canary_ref(event["secret_ref"]) and notice != DEPLOYMENT_TEST_NOTICE:
            superseded = not self.store.raise_flag(event["secret_ref"], event["flag_level"], now)
        extra = None
        if result.kind == CONTAINMENT_UPDATE:
            extra = _containment_facts(_since(facts or self.store.facts(event["agent_id"]),
                                              event_time_ms(event["event_id"])))
        inc = incident_mod.build(event, result, dashboard_url=self.config.dashboard_url, extra_facts=extra,
                                 notice=notice or self.notice)
        return inc, superseded

    # -- entry points --------------------------------------------------------------------
    def process_record(self, record, *, receive_count: int = 1, deadline: float | None = None) -> EventResult:
        event = parse(record)
        if event is None:
            return EventResult(None)
        notice = DEPLOYMENT_TEST_NOTICE if is_test_envelope(record) else None
        return self.process_event(event, receive_count=receive_count, deadline=deadline, notice=notice)

    def _remaining(self, deadline: float | None) -> float:
        return float("inf") if deadline is None else deadline - self.monotonic()

    def process_event(self, event: dict, *, receive_count: int = 1, deadline: float | None = None,
                      notice: str | None = None) -> EventResult:
        """deadline: monotonic time by which this invocation must stop (Lambda timeout minus margin).
        Destinations that would not fit are left for the redelivery, unclaimed, instead of being cut off
        mid-send by the Lambda timeout."""
        inc, superseded = self.incident_for(event, notice)
        result = EventResult(event["event_id"], inc.category, inc.severity.label)
        last_chance = receive_count >= self.max_receives
        for dest in self.config.destinations:
            if self._remaining(deadline) < DEST_BUDGET and route(dest, inc) is not None:
                result.outcomes.append(Outcome(dest.id, st.RETRYING, "deadline"))
                continue
            try:
                outcome = self._deliver_one(event, inc, dest, superseded, last_chance, deadline)
            except Exception as exc:  # never let one destination break the others
                log.error("destination %s internal error %s", dest.id, type(exc).__name__)
                outcome = self._finish(event, inc, dest, st.FAILED if last_chance else st.RETRYING,
                                       "retries_exhausted" if last_chance else "internal_error")
            if outcome:
                result.outcomes.append(outcome)
        return result

    # -- per destination -----------------------------------------------------------------
    def _deliver_one(self, event, inc: Incident, dest: Destination, superseded: bool, last_chance: bool,
                     deadline: float | None = None):
        decision = route(dest, inc)
        if decision is None:
            return None
        now = self.clock()
        claim = self.store.claim(event["event_id"], dest.id, dest.provider, now, self.config.delivery_ttl_days)
        if not claim.won:
            metrics.emit("DeliverySkipped", provider=dest.provider, outcome=f"duplicate_{claim.status}")
            return Outcome(dest.id, f"skipped_{claim.status}")
        if superseded:
            return self._finish(event, inc, dest, st.SUPPRESSED, "superseded_by_higher_flag_level")
        if decision == DIGEST:
            self._window(dest, "digest", inc, self.config.digest_window_seconds, now)
            return self._finish(event, inc, dest, st.SUPPRESSED, "digest")
        if claim.attempts == 1 and inc.severity < Severity.CRITICAL and self._aggregatable(inc):
            count = self._window(dest, f"{inc.category}#{inc.agent_id}", inc,
                                 self.config.aggregation_window_seconds, now)
            if count > self.config.individual_per_window:
                return self._finish(event, inc, dest, st.SUPPRESSED, "aggregated")
        if inc.severity < Severity.CRITICAL and not self.store.rate_take(
                dest.id, int(now // 60), dest.rate_per_minute, now):
            wait = 60 - (now % 60) + 1
            return self._retry_or_fail(event, inc, dest, "local_rate_limit", wait, last_chance)
        return self._send(event, inc, dest, last_chance, deadline)

    def _aggregatable(self, inc: Incident) -> bool:
        from .policy import NEVER_AGGREGATE
        return inc.category not in NEVER_AGGREGATE

    def _window(self, dest: Destination, key: str, inc: Incident, seconds: int, now: float) -> int:
        start = int(now // seconds * seconds)
        sample = {"event_id": inc.event_id, "ts": inc.ts, "severity": inc.severity.label, "category": inc.category,
                  "kind": inc.kind, "title": inc.title, "summary": inc.summary, "agent_id": inc.agent_id,
                  "role": inc.role, "notice": inc.notice}
        return self.store.window_add(dest.id, key, start, start + seconds, sample, now)

    def _send(self, event, inc: Incident, dest: Destination, last_chance: bool,
              deadline: float | None = None) -> Outcome:
        provider = self.providers[dest.provider]
        try:
            secret = self.secrets.get(dest.secret_ref) if provider.needs_secret else None
        except SecretError as err:
            metrics.emit("DeliveryFailed", provider=dest.provider, outcome="secret_unavailable")
            if getattr(err, "transient", False):
                return self._retry_or_fail(event, inc, dest, "secret_unavailable_transient", None, last_chance)
            return self._finish(event, inc, dest, st.FAILED, "secret_unavailable")
        try:
            prepared = provider.build(inc, dest)
        except Exception as exc:
            log.error("build failed for %s: %s", dest.id, type(exc).__name__)
            return self._finish(event, inc, dest, st.FAILED, "build_failed")
        attempt = 0
        while True:
            attempt += 1
            started = self.clock()
            try:
                ack = provider.send(prepared, dest, secret)
            except DeliveryError as err:
                quick = (err.retryable and attempt == 1 and (err.retry_after or 0) <= QUICK_RETRY_MAX_WAIT
                         and self._remaining(deadline) > DEST_BUDGET / 2)
                if quick:
                    sleep_for(err.retry_after if err.retry_after is not None else backoff(1, base=0.5, cap=1.0))
                    continue
                metrics.emit("DeliveryFailed", provider=dest.provider, outcome=err.category)
                if not err.retryable:
                    return self._finish(event, inc, dest, st.FAILED, err.category)
                return self._retry_or_fail(event, inc, dest, err.category, err.retry_after, last_chance)
            metrics.emit("Delivered", provider=dest.provider, outcome="delivered",
                         latency_ms=(self.clock() - started) * 1000)
            if inc.is_update and inc.aggregated_count == 1 and not inc.event_id.startswith("agg_"):
                self._schedule_summary(dest, inc)
            outcome = self._finish(event, inc, dest, st.DELIVERED, None, provider_ref=ack.provider_ref)
            outcome.echo = ack.echo
            return outcome

    def _schedule_summary(self, dest: Destination, inc: Incident) -> None:
        """Containment results (rotated/flagged/failed) are written after the revoke event, so a
        follow-up summary goes out at the next flush after SUMMARY_DELAY with whatever was recorded."""
        now = self.clock()
        base_facts = [list(f) for f in inc.facts if f[0] not in ("Owned credentials", "Shared credentials")]
        sample = {"event_id": inc.event_id, "ts": inc.ts, "severity": inc.severity.label, "category": inc.category,
                  "kind": inc.kind, "title": "Containment summary", "summary": inc.summary,
                  "agent_id": inc.agent_id, "role": inc.role, "session_id": inc.session_id, "facts": base_facts,
                  "notice": inc.notice}
        try:
            self.store.window_add(dest.id, f"containment#{inc.agent_id}#{inc.event_id}", int(now),
                                  int(now + SUMMARY_DELAY), sample, now)
        except Exception as exc:  # the immediate update already went out; the summary is best effort
            log.warning("could not schedule containment summary: %s", type(exc).__name__)

    def _retry_or_fail(self, event, inc, dest, category, retry_after, last_chance) -> Outcome:
        if last_chance:
            return self._finish(event, inc, dest, st.FAILED, f"retries_exhausted:{category}"[:60])
        return self._finish(event, inc, dest, st.RETRYING, category, retry_after=retry_after)

    def _finish(self, event, inc, dest, status, category, *, provider_ref=None, retry_after=None) -> Outcome:
        if status == st.FAILED:
            metrics.emit("PermanentFailure", provider=dest.provider, outcome=category or "unknown")
        self.store.finish(event["event_id"], dest.id, status, self.clock(), category=category,
                          provider_ref=provider_ref, retry_after=retry_after)
        return Outcome(dest.id, status, category, retry_after)

    # -- aggregation / digest flush (scheduled) ------------------------------------------
    def flush_windows(self, deadline: float | None = None) -> list[Outcome]:
        now = self.clock()
        outcomes = []
        for window in self.store.due_windows(now):
            if self._remaining(deadline) < DEST_BUDGET:
                break  # the rest stay due; the next scheduled flush continues
            dest_id = window["destination_id"]
            try:
                dest = self.config.destination(dest_id)
            except KeyError:
                continue
            if window["key"].startswith("containment#"):
                claimed = self.store.claim_window(window["pk"], now)
                inc = self._containment_summary(claimed) if claimed and dest.enabled else None
                if inc is None:
                    continue
            else:
                is_digest = window["key"] == "digest"
                suppressed = window["count"] if is_digest else window["count"] - self.config.individual_per_window
                if suppressed <= 0 or not dest.enabled:
                    self.store.claim_window(window["pk"], now)
                    continue
                claimed = self.store.claim_window(window["pk"], now)
                if not claimed:
                    continue
                inc = self._summary_incident(claimed, suppressed, is_digest)
            pseudo_event = {"event_id": inc.event_id}
            try:
                claim = self.store.claim(inc.event_id, dest.id, dest.provider, now, self.config.delivery_ttl_days)
                if not claim.won:
                    continue
                last = claim.attempts >= self.max_receives
                if inc.severity < Severity.CRITICAL and not self.store.rate_take(
                        dest.id, int(now // 60), dest.rate_per_minute, now):
                    outcome = self._retry_or_fail(pseudo_event, inc, dest, "local_rate_limit", 60.0, last)
                else:
                    outcome = self._send(pseudo_event, inc, dest, last_chance=last)
            except Exception as exc:  # one window never stops the flush, and never stays "sending"
                log.error("flush of a window for %s failed: %s", dest.id, type(exc).__name__)
                try:
                    outcome = self._finish(pseudo_event, inc, dest, st.RETRYING, "internal_error")
                except Exception:
                    outcome = Outcome(dest.id, st.RETRYING, "internal_error")
            if outcome.status == st.RETRYING:
                try:
                    self.store.reopen_window(window["pk"])  # the next flush tries again (bounded by attempts)
                except Exception as exc:
                    log.error("could not reopen a window for %s: %s", dest.id, type(exc).__name__)
            outcomes.append(outcome)
            self.flushed.append((dest.id, inc))
        return outcomes

    def _containment_summary(self, window: dict) -> Incident | None:
        sample = window["sample"]
        facts = _since(self.store.facts(sample["agent_id"]), event_time_ms(sample["event_id"]))
        # Always sent: "nothing recorded since the revoke" is itself an outcome a person must check
        # (a manual burn whose rotation errors writes no event at all).
        rows = [tuple(f) for f in sample.get("facts", [])] + _containment_facts(facts, final=True)
        return Incident(
            event_id="agg_" + hashlib.sha256(window["pk"].encode()).hexdigest()[:24],
            incident_id=incident_mod.incident_id("summary:" + sample["event_id"]), ts=sample["ts"],
            severity=Severity.CRITICAL if facts.rotation_failed else Severity.parse(sample["severity"]),
            category=sample["category"], kind=CONTAINMENT_UPDATE, title="Containment summary",
            summary="What Burn recorded for this containment after the session was revoked. "
                    "Counts cover events received so far.",
            agent_id=sample["agent_id"], session_id=sample.get("session_id"), role=sample.get("role"),
            flag_level=None, facts=tuple(rows), priority=bool(facts.rotation_failed),
            dashboard_url=self.config.dashboard_url, notice=sample.get("notice") or self.notice)

    def _summary_incident(self, window: dict, suppressed: int, is_digest: bool) -> Incident:
        sample = window["sample"]
        label = time.strftime("%H:%M", time.gmtime(window["window_start"])) + "-" + \
            time.strftime("%H:%M UTC", time.gmtime(window["window_end"]))
        base = Incident(
            event_id="agg_" + hashlib.sha256(window["pk"].encode()).hexdigest()[:24], incident_id="", ts=sample["ts"], severity=Severity.parse(sample["severity"]),
            category=sample["category"], kind=sample["kind"], title=sample["title"], summary=sample["summary"],
            agent_id=sample["agent_id"], session_id=None, role=sample.get("role"), flag_level=None,
            dashboard_url=self.config.dashboard_url, notice=sample.get("notice") or self.notice)
        if is_digest:
            base = replace(base, title="Low-severity digest", category="digest",
                           agent_id="multiple instances", role=None)
        return incident_mod.aggregate(base, suppressed, label)
