"""Replace on burn, at burn time: the `replacement` record a burned instance gets (moto + fake minter).

Progress and generation are written straight onto the session row here, in the shape the heartbeat stores
them, so these tests cover only what the burn records.
"""
import statistics
import time

import pytest
from burn_contracts import names
from burn_contracts.events import make_event
from conftest import call
from test_swarm import WORKER, make_role, session_row, spawn

import replace


def replacing_role(env, spec=WORKER, replace_on_burn=True):
    make_role(env, spec)
    if replace_on_burn is not None:
        env.ddb.Table("burn-roles").update_item(
            Key={"name": spec["name"]}, UpdateExpression="SET replace_on_burn = :r",
            ExpressionAttributeValues={":r": replace_on_burn})


def instances(env, n, role="worker"):
    token = call(env, "POST", f"/roles/{role}/tokens", {"uses": n})[1]["token"]
    return [call(env, "POST", "/sessions", {"role": role, "enroll_token": token})[1] for _ in range(n)]


def set_row(env, session_id, **fields):
    env.ddb.Table("burn-sessions").update_item(
        Key={"session_id": session_id},
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
        ExpressionAttributeNames={f"#{k}": k for k in fields},
        ExpressionAttributeValues={f":{k}": v for k, v in fields.items()})


def started(at, done=None):
    return {"started_at": at, "done_at": done}


def burn_instance(env, session_id):
    status, out = call(env, "POST", "/burns", {"scope": "instance", "id": session_id})
    assert status == 200 and out["failed"] == [], out
    return out


def replacement_of(env, session_id):
    return session_row(env, session_id).get("replacement")


def add_event(env, agent_id, session_id, action, ts, source="gateway", **fields):
    """An event as the Gateway or egress proxy writes it. A blocked event that stopped a credential names it
    (here the instance's own key, which a burn rotates rather than flags)."""
    if action == "blocked" and not fields.get("policy_only"):
        fields = {"secret_ref": f"burn/{agent_id}/api_key", "secret_type": "api_key", "owner": "agent"} | fields
    fields.pop("policy_only", None)
    event = make_event(agent_id=agent_id, source=source, action=action, session_id=session_id, **fields) | {"ts": ts}
    env.ddb.Table("burn-events").put_item(Item={"stream": names.EVENTS_STREAM, **event})


EARLY = "2026-01-01T00:00:00.000Z"  # well before any burn in these tests
LATE = "2099-01-01T00:00:00.000Z"   # well after


# --- pending, needs_person, and burns that never replace -----------------------------------------

def test_an_instance_burn_marks_a_pending_replacement_cut_off_at_the_burn(env):
    replacing_role(env)
    [inst] = instances(env, 1)
    set_row(env, inst["session_id"], progress={
        "a": started(EARLY, EARLY), "b": started(EARLY), "c": started(LATE)})
    out = burn_instance(env, inst["session_id"])

    record = replacement_of(env, inst["session_id"])
    assert record == {"status": "pending", "cutoff": session_row(env, inst["session_id"])["revoked_at"],
                      "cutoff_source": "burn_time", "done": ["a"], "quarantined": ["b", "c"]}
    # The burn's own reply is what it always was.
    assert set(out["burned"][0]) == {"agent_id", "revoked", "sessions_revoked", "rotated", "flagged"}


def test_an_instance_with_no_progress_still_gets_an_empty_pending_replacement(env):
    replacing_role(env)
    [inst] = instances(env, 1)
    burn_instance(env, inst["session_id"])
    record = replacement_of(env, inst["session_id"])
    assert (record["status"], record["done"], record["quarantined"]) == ("pending", [], [])


@pytest.mark.parametrize("generation, status", [(0, "pending"), (2, "pending"), (3, "needs_person"),
                                                (7, "needs_person")])
def test_a_replacement_at_the_generation_limit_needs_a_person(env, generation, status):
    assert names.MAX_REPLACEMENT_GENERATION == 3
    replacing_role(env)
    [inst] = instances(env, 1)
    set_row(env, inst["session_id"], generation=generation, progress={"a": started(EARLY, EARLY), "b": started(EARLY)})
    burn_instance(env, inst["session_id"])
    record = replacement_of(env, inst["session_id"])
    assert record["status"] == status
    assert (record["done"], record["quarantined"]) == (["a"], ["b"])  # the same lists either way


@pytest.mark.parametrize("replace_on_burn", [None, False])
def test_a_role_without_replace_on_burn_marks_nothing(env, replace_on_burn):
    replacing_role(env, replace_on_burn=replace_on_burn)  # None: the attribute is absent (default false)
    [inst] = instances(env, 1)
    burn_instance(env, inst["session_id"])
    assert replacement_of(env, inst["session_id"]) is None


@pytest.mark.parametrize("scope", ["role", "lineage", "token"])
def test_role_lineage_and_token_burns_mark_nothing(env, scope):
    replacing_role(env, WORKER | {"may_spawn": ["worker"]})
    token = call(env, "POST", "/roles/worker/tokens", {"uses": 2})[1]
    root, other = (call(env, "POST", "/sessions", {"role": "worker", "enroll_token": token["token"]})[1]
                   for _ in range(2))
    spawn(env, root["session_id"], "worker")
    ident = {"role": "worker", "lineage": root["session_id"], "token": token["token_id"]}[scope]
    status, out = call(env, "POST", "/burns", {"scope": scope, "id": ident})
    assert status == 200 and out["failed"] == []
    revoked = [s for b in out["burned"] for s in b["sessions_revoked"]]
    assert len(revoked) >= 2
    for session_id in revoked:
        assert "replacement" not in session_row(env, session_id)


def test_burning_an_agent_by_id_marks_nothing(env):
    """POST /burn {agent_id} contains the agent; only the single-instance burns replace."""
    replacing_role(env)
    [inst] = instances(env, 1)
    assert call(env, "POST", "/burn", {"agent_id": inst["agent_id"]})[0] == 200
    assert replacement_of(env, inst["session_id"]) is None


def test_scenario_agents_have_no_role_and_are_never_replaced(env):
    session = call(env, "POST", "/sessions", {"agent_id": "agent-a"})[1]
    burn_instance(env, session["session_id"])
    assert replacement_of(env, session["session_id"]) is None


def test_a_row_that_holds_a_null_replacement_still_gets_one(env):
    replacing_role(env)
    [inst] = instances(env, 1)
    set_row(env, inst["session_id"], replacement=None, generation=0)
    burn_instance(env, inst["session_id"])
    assert replacement_of(env, inst["session_id"])["status"] == "pending"


def test_exactly_one_replacement_per_instance_burned(env):
    replacing_role(env)
    victim, bystander = instances(env, 2)
    burn_instance(env, victim["session_id"])
    rows = env.ddb.Table("burn-sessions").scan()["Items"]
    assert [r["session_id"] for r in rows if "replacement" in r] == [victim["session_id"]]


# --- idempotent: a later burn never rewrites or adds one -----------------------------------------

def test_burning_the_instance_again_does_not_overwrite_its_replacement(env):
    replacing_role(env)
    [inst] = instances(env, 1)
    burn_instance(env, inst["session_id"])
    first = replacement_of(env, inst["session_id"])
    set_row(env, inst["session_id"], progress={"late": started(EARLY, EARLY)})
    burn_instance(env, inst["session_id"])
    assert replacement_of(env, inst["session_id"]) == first


def test_a_role_burned_instance_burned_again_alone_still_gets_none(env):
    """Only the burn that revoked an instance decides; burning it again later does not add a replacement."""
    replacing_role(env)
    [inst] = instances(env, 1)
    call(env, "POST", "/burns", {"scope": "role", "id": "worker"})
    burn_instance(env, inst["session_id"])
    assert replacement_of(env, inst["session_id"]) is None


def test_the_write_is_conditional_so_a_second_mark_never_overwrites(env):
    replacing_role(env)
    [inst] = instances(env, 1)
    burn_instance(env, inst["session_id"])
    first = replacement_of(env, inst["session_id"])
    assert replace.mark(inst["session_id"], evidence_time=EARLY) is None  # lost the conditional write
    assert replacement_of(env, inst["session_id"]) == first


def test_an_active_or_exited_instance_is_never_marked(env):
    replacing_role(env)
    active, exited = instances(env, 2)
    assert call(env, "POST", f"/instances/{exited['session_id']}/exit")[0] == 200
    for inst in (active, exited):
        assert replace.mark(inst["session_id"]) is None
        assert replacement_of(env, inst["session_id"]) is None


# --- the clean cut-off ---------------------------------------------------------------------------

def test_a_blocked_call_before_the_burn_is_the_cutoff(env):
    replacing_role(env)
    [inst] = instances(env, 1)
    set_row(env, inst["session_id"], progress={
        "before": started("2026-11-07T19:00:00.000Z", "2026-11-07T19:30:00.000Z"),
        "after": started("2026-11-07T19:00:00.000Z", "2026-11-07T20:30:00.000Z")})
    add_event(env, inst["agent_id"], inst["session_id"], "blocked", "2026-11-07T20:00:00.000Z", source="egress")
    add_event(env, inst["agent_id"], inst["session_id"], "blocked", "2026-11-07T20:10:00.000Z")
    burn_instance(env, inst["session_id"])
    record = replacement_of(env, inst["session_id"])
    assert (record["cutoff"], record["cutoff_source"]) == ("2026-11-07T20:00:00.000Z", "evidence")
    assert (record["done"], record["quarantined"]) == (["before"], ["after"])


def test_a_blocked_policy_denial_is_not_evidence_but_a_blocked_credential_is(env):
    """Most blocked events are policy denials (destination not allowed, payload too large, ...) with no credential
    in them; one harmless denied request must not quarantine everything after it."""
    replacing_role(env)
    denied, leaked, fingerprinted = instances(env, 3)
    add_event(env, denied["agent_id"], denied["session_id"], "blocked", EARLY, source="egress", policy_only=True,
              detail="destination_not_allowed")
    add_event(env, leaked["agent_id"], leaked["session_id"], "blocked", EARLY, source="egress")
    add_event(env, fingerprinted["agent_id"], fingerprinted["session_id"], "blocked", EARLY, source="egress",
              secret_ref=None, fingerprint="b" * 64)  # a credential of unknown format: fingerprint only
    for inst in (denied, leaked, fingerprinted):
        burn_instance(env, inst["session_id"])
    assert replacement_of(env, denied["session_id"])["cutoff_source"] == "burn_time"
    for inst in (leaked, fingerprinted):
        assert (replacement_of(env, inst["session_id"])["cutoff"],
                replacement_of(env, inst["session_id"])["cutoff_source"]) == (EARLY, "evidence")


def test_a_refused_call_before_the_burn_is_the_cutoff(env):
    replacing_role(env)
    [inst] = instances(env, 1)
    add_event(env, inst["agent_id"], inst["session_id"], "refused", "2026-11-07T20:05:00.000Z")
    burn_instance(env, inst["session_id"])
    record = replacement_of(env, inst["session_id"])
    assert (record["cutoff"], record["cutoff_source"]) == ("2026-11-07T20:05:00.000Z", "evidence")


def test_evidence_after_the_burn_or_from_another_session_is_not_the_cutoff(env):
    replacing_role(env)
    [inst] = instances(env, 1)
    add_event(env, inst["agent_id"], inst["session_id"], "refused", LATE)  # e.g. a call after the burn
    add_event(env, inst["agent_id"], "sess_someoneelse", "blocked", EARLY)
    add_event(env, inst["agent_id"], inst["session_id"], "recorded", EARLY, source="tool_response")  # not evidence
    burn_instance(env, inst["session_id"])
    record = replacement_of(env, inst["session_id"])
    assert record["cutoff_source"] == "burn_time"
    assert record["cutoff"] == session_row(env, inst["session_id"])["revoked_at"]


# --- classification boundaries (via a known evidence time) ---------------------------------------

CUTOFF = "2026-11-07T20:00:00.000Z"


def test_classification_boundaries(env):
    replacing_role(env)
    [inst] = instances(env, 1)
    set_row(env, inst["session_id"], progress={
        "done-just-before": started("2026-11-07T19:00:00.000Z", "2026-11-07T19:59:59.999Z"),
        "done-other-spelling": started("2026-11-07T19:00:00+00:00", "2026-11-07T19:59:59.998+00:00"),
        "done-at-cutoff": started("2026-11-07T19:00:00.000Z", CUTOFF),           # equal is not before
        "in-flight": started("2026-11-07T19:00:00.000Z"),                         # never done
        "done-after": started("2026-11-07T19:00:00.000Z", "2026-11-07T20:00:00.001Z"),
        "started-at-cutoff": started(CUTOFF, "2026-11-07T20:00:05.000Z"),
        "started-after": started("2026-11-07T20:01:00.000Z"),
    })
    add_event(env, inst["agent_id"], inst["session_id"], "blocked", CUTOFF)
    burn_instance(env, inst["session_id"])
    record = replacement_of(env, inst["session_id"])
    assert (record["cutoff"], record["cutoff_source"]) == (CUTOFF, "evidence")
    assert record["done"] == ["done-just-before", "done-other-spelling"]
    assert record["quarantined"] == ["done-after", "done-at-cutoff", "in-flight", "started-after",
                                     "started-at-cutoff"]


# --- best effort ---------------------------------------------------------------------------------

def test_runs_after_revoke_and_rotate(env, monkeypatch):
    replacing_role(env)
    [inst] = instances(env, 1)
    seen = {}
    real = replace.mark

    def spy(session_id, evidence_time=None):
        seen["status"] = session_row(env, session_id)["status"]
        seen["rotated"] = ("POST", f"/agents/{inst['agent_id']}/rotate") in [c[:2] for c in env.minter.calls]
        return real(session_id, evidence_time)

    monkeypatch.setattr(replace, "mark", spy)
    burn_instance(env, inst["session_id"])
    assert seen == {"status": "revoked", "rotated": True}


def test_a_failing_hook_becomes_an_event_and_leaves_the_burn_unchanged(env, monkeypatch):
    replacing_role(env)
    a, b = instances(env, 2)
    clean = burn_instance(env, a["session_id"])

    def broken(session_id, evidence_time=None):
        raise RuntimeError("secret detail that must not reach the ledger")

    monkeypatch.setattr(replace, "mark", broken)
    out = burn_instance(env, b["session_id"])
    assert out["failed"] == [] and set(out["burned"][0]) == set(clean["burned"][0])
    assert out["burned"][0]["sessions_revoked"] == [b["session_id"]]
    assert session_row(env, b["session_id"])["status"] == "revoked"
    assert replacement_of(env, b["session_id"]) is None

    events = [e for e in env.ddb.Table("burn-events").scan()["Items"]
              if e["agent_id"] == b["agent_id"] and e["action"] == "flagged"]
    [event] = events
    assert event["source"] == "burn" and event["session_id"] == b["session_id"]
    assert "replacement" in event["detail"] and "RuntimeError" in event["detail"]
    assert "secret detail" not in event["detail"]
    assert event["actor"] == "admin"  # the caller of the burn (demo token = admin)


def test_a_failing_hook_whose_event_also_fails_still_leaves_the_burn_unchanged(env, monkeypatch):
    replacing_role(env)
    [inst] = instances(env, 1)
    real_write = replace.db.write_event

    def broken(*args, **kwargs):
        raise RuntimeError("down")

    def write_event(**fields):
        if "replacement" in (fields.get("detail") or ""):
            raise RuntimeError("events down too")
        return real_write(**fields)

    monkeypatch.setattr(replace, "mark", broken)
    monkeypatch.setattr(replace.db, "write_event", write_event)
    out = burn_instance(env, inst["session_id"])
    assert out["burned"][0]["sessions_revoked"] == [inst["session_id"]]


# --- cost: the instance and its role; if it replaces, its evidence and one conditional write --------

def _count_calls(client):
    counted = []
    client.meta.events.register("before-call.dynamodb", lambda model, **_: counted.append(model.name))
    return counted


def test_the_hook_costs_few_dynamodb_calls(env):
    import db
    import sessions
    replacing_role(env)
    plain, replacing = instances(env, 2)
    set_row(env, plain["session_id"], role="other")  # a role that is not stored: no replace_on_burn
    for inst in (plain, replacing):
        sessions.end_session(session_row(env, inst["session_id"]), "revoked")
    counted = _count_calls(db.resource().meta.client)
    replace.after_burn(plain["agent_id"], [plain["session_id"]], [], None)
    assert counted == ["GetItem", "GetItem"]  # the instance, its role: nothing to replace
    counted.clear()
    replace.after_burn(replacing["agent_id"], [replacing["session_id"]], [], None)
    assert counted == ["GetItem", "GetItem", "Query", "UpdateItem"]  # + its evidence, + the conditional write


def test_instance_burn_timing_with_and_without_the_hook(env, monkeypatch):
    """Prints the median time of an instance burn on moto, with the hook and with it switched off (run with -s).

    Information only: wall-clock times vary with machine load, so the cost is pinned by the call-count test above.
    """
    replacing_role(env)
    runs = 15
    victims = instances(env, 2 * runs)
    real = replace.after_burn

    def timed(group):
        times = []
        for inst in group:
            start = time.perf_counter()
            burn_instance(env, inst["session_id"])
            times.append((time.perf_counter() - start) * 1000)
        return statistics.median(times)

    timed(victims[:2])  # warm up
    monkeypatch.setattr(replace, "after_burn", lambda *a, **k: None)
    without = timed(victims[2:runs + 1])
    monkeypatch.setattr(replace, "after_burn", real)
    with_hook = timed(victims[runs + 1:])
    print(f"\ninstance burn median on moto: {without:.1f} ms without the hook, {with_hook:.1f} ms with it")
    assert replacement_of(env, victims[-1]["session_id"])["status"] == "pending"  # the hook really ran


# --- a burn by source ----------------------------------------------------------------------------

from test_by_source import SOURCE, ledger, row  # noqa: E402,F401 (ledger is a fixture)


def test_a_burn_by_source_cuts_off_at_each_instances_first_read(env, ledger):
    replacing_role(env)
    reader, messaged = instances(env, 2)
    progress = {"a": started("2026-11-07T19:00:00.000Z", "2026-11-07T19:59:00.000Z"),
                "b": started("2026-11-07T19:00:00.000Z", "2026-11-07T20:02:00.000Z")}
    for inst in (reader, messaged):
        set_row(env, inst["session_id"], progress=progress)
    row(ledger, reader["agent_id"], "burn/shared/crm_db_url", source_ref=SOURCE, first_seen="2026-11-07T20:00:00.000Z")
    row(ledger, messaged["agent_id"], "burn/shared/crm_db_url", source_ref=SOURCE,
        first_seen="2026-11-07T20:03:00.000Z", source="handoff", via=reader["agent_id"])
    status, out = call(env, "POST", "/burn/by-source", {"source_ref": SOURCE})
    assert status == 200 and out["failed"] == []

    first, second = replacement_of(env, reader["session_id"]), replacement_of(env, messaged["session_id"])
    assert (first["cutoff"], first["cutoff_source"]) == ("2026-11-07T20:00:00.000Z", "evidence")
    assert (first["done"], first["quarantined"]) == (["a"], ["b"])
    assert (second["cutoff"], second["cutoff_source"]) == ("2026-11-07T20:03:00.000Z", "evidence")
    assert (second["done"], second["quarantined"]) == (["a", "b"], [])


def test_the_earliest_evidence_wins(env, ledger):
    replacing_role(env)
    [inst] = instances(env, 1)
    row(ledger, inst["agent_id"], "burn/shared/crm_db_url", source_ref=SOURCE, first_seen="2026-11-07T20:00:00.000Z")
    add_event(env, inst["agent_id"], inst["session_id"], "blocked", "2026-11-07T19:45:00.000Z")
    call(env, "POST", "/burn/by-source", {"source_ref": SOURCE})
    assert replacement_of(env, inst["session_id"])["cutoff"] == "2026-11-07T19:45:00.000Z"


# --- a canary burn (the provider -> Burn API -> minter path, in process) -------------------------

from test_canary_containment import VICTIM, fleet, leak_and_present, stack  # noqa: E402,F401 (fixtures)


def _row(stack, session_id):
    return stack.table("burn-sessions").get_item(Key={"session_id": session_id})["Item"]


def test_a_canary_burn_cuts_off_at_the_canary_use(stack, fleet):
    victim = fleet[VICTIM]
    stack.set_role(victim["session_id"], "worker", replace_on_burn=True)
    leak_and_present(stack, victim)
    [used] = stack.events("canary_used")
    record = _row(stack, victim["session_id"])["replacement"]
    assert record["status"] == "pending"
    assert (record["cutoff"], record["cutoff_source"]) == (used["ts"], "evidence")
    others = [s for agent_id, s in fleet.items() if agent_id != VICTIM]
    assert all("replacement" not in _row(stack, s["session_id"]) for s in others)


def test_a_canary_burn_falls_back_to_when_burn_received_the_incident(stack, fleet, monkeypatch):
    """If the canary use is not readable yet, the incident's arrival is still evidence, and before the burn."""
    from burn_contracts.progress import parse_time
    victim = fleet[VICTIM]
    stack.set_role(victim["session_id"], "worker", replace_on_burn=True)
    monkeypatch.setattr(replace, "_evidence", lambda agent_id, session_id: [])
    leak_and_present(stack, victim)
    [used] = stack.events("canary_used")
    row_ = _row(stack, victim["session_id"])
    record = row_["replacement"]
    assert record["cutoff_source"] == "evidence"
    assert parse_time(used["ts"]) <= parse_time(record["cutoff"]) <= parse_time(row_["revoked_at"])
