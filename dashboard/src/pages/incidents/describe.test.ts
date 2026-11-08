import { describe, expect, it } from "vitest";
import type { Instance, LedgerEvent } from "../../contracts";
import {
  consoleValue,
  event,
  flag,
  instance,
  today,
} from "../../features/flags/testConsole";
import {
  burnCause,
  burnedTodayLine,
  burnWhen,
  causeCounts,
  causeSentence,
  keysWaitingLine,
  nameList,
  outcomeSentence,
  statusLabel,
  stillOpenBody,
} from "./describe";

const SHARED = "burn/shared/crm_db_url";

/** A console with these instances and events (oldest first), and its incidents. */
function world(instances: Instance[], events: LedgerEvent[], flags = [flag()]) {
  const value = consoleValue({ instances, events, flags });
  return {
    value,
    model: value.model,
    events: value.events,
    incident: (session: string) =>
      value.incidents.find((item) => item.agent.sessionId === session)!,
  };
}

const burned = (session: string, extra: Partial<Instance> = {}) =>
  instance(session, "revoked", { created_at: today(1, 0), ...extra });

describe("why an agent was burned", () => {
  it("names the person and their reason", () => {
    const w = world(
      [burned("s7")],
      [
        event({
          action: "revoked",
          agent_id: "agent-worker-s7",
          session_id: "s7",
          ts: today(19, 42),
          actor: "priya.nair@example.com",
          detail: "1 session(s) revoked (read an unverified vendor page)",
        }),
      ],
    );
    const cause = burnCause(w.incident("s7").agent, w.events);
    expect(cause).toEqual({
      kind: "person",
      actor: "priya.nair@example.com",
      reason: "read an unverified vendor page",
    });
    expect(causeSentence(cause)).toBe(
      "Burned by priya.nair@example.com: “read an unverified vendor page”",
    );
  });

  it("calls the local admin by name and leaves out a missing reason", () => {
    const w = world(
      [burned("s7")],
      [
        event({
          action: "revoked",
          agent_id: "agent-worker-s7",
          session_id: "s7",
          actor: "admin",
          detail: "1 session(s) revoked",
        }),
      ],
    );
    expect(causeSentence(burnCause(w.incident("s7").agent, w.events))).toBe(
      "Burned by the local admin",
    );
  });

  it("reads a canary burn, with the address the canary was used from", () => {
    const events = [
      event({
        action: "canary_used",
        source: "provider",
        agent_id: "agent-worker-s7",
        session_id: "s7",
        ts: today(19, 41),
        detail:
          "canary of session s7 presented at the provider from outside Burn; rejected (from 203.0.113.9)",
      }),
      event({
        action: "revoked",
        agent_id: "agent-worker-s7",
        session_id: "s7",
        ts: today(19, 42),
        actor: "system",
        secret_ref: "burn/agent-worker-s7/canary_1",
        detail:
          "canary incident evt_X: session revoked; canary first read via read_config at 2026-11-08T19:30:00Z",
      }),
    ];
    const w = world([burned("s7")], events);
    const cause = burnCause(w.incident("s7").agent, w.events);
    expect(cause).toEqual({ kind: "canary", ip: "203.0.113.9" });
    expect(causeSentence(cause)).toBe(
      "Its canary was used from 203.0.113.9, outside Burn. Burned automatically",
    );
    expect(causeSentence({ kind: "canary" })).toBe(
      "Its canary was used, outside Burn. Burned automatically",
    );
  });

  it("counts the others a source burn took with it", () => {
    const revoke = (session: string, seconds: number) =>
      event({
        action: "revoked",
        agent_id: `agent-worker-${session}`,
        session_id: session,
        ts: today(11, 2, seconds),
        actor: "priya.nair@example.com",
        detail: "1 session(s) revoked (read read_ticket:T-4410)",
      });
    const w = world(
      [burned("s1"), burned("s2"), burned("s3")],
      [revoke("s1", 0), revoke("s2", 1), revoke("s3", 2)],
    );
    const cause = burnCause(w.incident("s2").agent, w.events);
    expect(cause).toMatchObject({
      kind: "source",
      sourceRef: "read_ticket:T-4410",
      others: 2,
    });
    expect(causeSentence(cause)).toBe(
      "Read read_ticket:T-4410; burned with the 2 others that read it",
    );
    expect(
      causeSentence({
        kind: "source",
        sourceRef: "read_ticket:T-1",
        others: 0,
      }),
    ).toBe("Read read_ticket:T-1; burned as the only agent that read it");
  });

  it("doesn't mistake a person's reason that starts with 'read' for a source burn", () => {
    const w = world(
      [burned("s7")],
      [
        event({
          action: "revoked",
          agent_id: "agent-worker-s7",
          session_id: "s7",
          actor: "priya.nair@example.com",
          detail: "1 session(s) revoked (read an unverified vendor page)",
        }),
      ],
    );
    expect(burnCause(w.incident("s7").agent, w.events).kind).toBe("person");
  });

  it("says so when nobody is recorded, or the burn is older than the events", () => {
    const w = world(
      [burned("s7"), burned("s8")],
      [
        event({
          action: "revoked",
          agent_id: "agent-worker-s7",
          detail: "Mock badge revocation.",
        }),
      ],
    );
    expect(causeSentence(burnCause(w.incident("s7").agent, w.events))).toBe(
      "Burned. No reason was recorded",
    );
    expect(burnCause(w.incident("s8").agent, w.events).kind).toBe("unrecorded");
  });

  it("uses only the burn of this agent's own session", () => {
    const w = world(
      [
        burned("s7", { agent_id: "agent-reader" }),
        instance("s9", "active", { agent_id: "agent-reader" }),
      ],
      [
        event({
          action: "revoked",
          agent_id: "agent-reader",
          session_id: "s-other",
          actor: "admin",
        }),
      ],
    );
    expect(burnCause(w.incident("s7").agent, w.events).kind).toBe("unrecorded");
  });
});

describe("what Burn did", () => {
  const burnOf = (more: LedgerEvent[], extra: Partial<Instance> = {}) =>
    world(
      [
        burned("s7", extra),
        instance("s18", "active", { created_at: today(20, 0) }),
      ],
      [
        event({
          action: "revoked",
          agent_id: "agent-worker-s7",
          session_id: "s7",
          ts: today(19, 42),
        }),
        ...more,
      ],
    );
  const own = (action: "rotated" | "flagged", ref: string, detail?: string) =>
    event({
      action,
      agent_id: "agent-worker-s7",
      ts: today(19, 42),
      secret_ref: ref,
      detail,
    });

  it("lists the keys it rotated, the shared key it flagged and the replacement", () => {
    const w = burnOf(
      [
        own("rotated", "burn/agent-worker-s7/api_key"),
        own("rotated", "burn/agent-worker-s7/aws_key"),
        own("flagged", SHARED),
      ],
      { replaced_by: "s18" },
    );
    expect(outcomeSentence(w.incident("s7"), w.events, w.model)).toBe(
      "Badge revoked, 2 owned keys rotated, crm_db_url sent to its owner, replaced by worker-02",
    );
  });

  it("says a burn flagged no shared key, but only when the burn is in the events", () => {
    const w = burnOf([own("rotated", "burn/agent-worker-s7/api_key")]);
    expect(outcomeSentence(w.incident("s7"), w.events, w.model)).toBe(
      "Badge revoked, 1 owned key rotated, no shared key to flag",
    );
    const old = world([burned("s7")], []);
    expect(outcomeSentence(old.incident("s7"), old.events, old.model)).toBe(
      "Badge revoked",
    );
  });

  it("reports a rotation that failed, even when the event names no key", () => {
    const w = burnOf([
      event({
        action: "flagged",
        agent_id: "agent-worker-s7",
        ts: today(19, 42),
        detail:
          "rotation failed: session stays revoked, credentials NOT rotated; containment incomplete, burn the agent manually",
      }),
    ]);
    expect(outcomeSentence(w.incident("s7"), w.events, w.model)).toBe(
      "Badge revoked, rotation failed: finish it by hand, no shared key to flag",
    );
  });

  it("says where the replacement stands", () => {
    const pending = burnOf([], {
      replacement: {
        status: "needs_person",
        cutoff: today(19, 42),
        cutoff_source: "burn_time",
        done: [],
        quarantined: [],
      },
    });
    expect(
      outcomeSentence(pending.incident("s7"), pending.events, pending.model),
    ).toBe("Badge revoked, no shared key to flag, replacement needs a person");
  });
});

describe("an incident's status", () => {
  it("is open while a key waits, and Burned once nothing does", () => {
    const flagged = world(
      [burned("s7")],
      [
        event({
          action: "revoked",
          agent_id: "agent-worker-s7",
          session_id: "s7",
        }),
        event({
          action: "flagged",
          agent_id: "agent-worker-s7",
          secret_ref: SHARED,
        }),
      ],
    );
    expect(statusLabel(flagged.incident("s7"))).toEqual({
      text: "Key open",
      tone: "flag",
    });
    const decided = world([burned("s7")], flagged.events.slice().reverse(), [
      flag({ state: "dismissed" }),
    ]);
    expect(statusLabel(decided.incident("s7"))).toEqual({
      text: "Burned",
      tone: "neutral",
    });
  });
});

describe("row details", () => {
  const now = new Date(today(20, 0));
  const w = world([burned("s7")], []);
  const agent = w.incident("s7").agent;

  it("says when it was burned, and where that time came from", () => {
    expect(
      burnWhen(
        { ...agent, burnedAt: today(19, 42), burnedAtFrom: "event" },
        now,
      ),
    ).toMatch(/^worker, today 7:42\sPM$/);
    expect(
      burnWhen(
        {
          ...agent,
          burnedAt: "2026-11-06T15:15:00",
          burnedAtFrom: "replacement",
        },
        now,
      ),
    ).toMatch(/^worker, Nov 6, 3:15\sPM$/);
    expect(
      burnWhen(
        { ...agent, burnedAt: today(9, 0), burnedAtFrom: "last-seen" },
        now,
      ),
    ).toMatch(/^worker, last seen 9:00\sAM$/);
    expect(burnWhen({ ...agent, burnedAt: undefined }, now)).toBe("worker");
  });

  it("counts burns by how they started, one per agent", () => {
    expect(
      causeCounts([
        { kind: "canary" },
        { kind: "canary" },
        { kind: "person" },
        { kind: "source" },
        { kind: "unattributed" },
        { kind: "unrecorded" },
      ]),
    ).toEqual({ canary: 2, person: 1, source: 1, unknown: 2 });
  });

  it("writes the hero's lines and short name lists", () => {
    expect(burnedTodayLine(0)).toBe("No agent burned today.");
    expect(burnedTodayLine(1)).toBe("1 agent burned today.");
    expect(burnedTodayLine(3)).toBe("3 agents burned today.");
    expect(keysWaitingLine(0)).toBe("No key waits on its owner.");
    expect(keysWaitingLine(1)).toBe("1 key waits on its owner.");
    expect(keysWaitingLine(2)).toBe("2 keys wait on their owners.");
    expect(nameList(["a"])).toBe("a");
    expect(nameList(["a", "b"])).toBe("a and b");
    expect(nameList(["a", "b", "c", "d"])).toBe("a, b and 2 more");
  });
});

describe("a key still open", () => {
  it("says which incident it came from, whose it is and who else saw it", () => {
    const w = world(
      [
        burned("s7"),
        instance("s8", "active", { created_at: today(2, 0) }),
        instance("s9", "active", { created_at: today(3, 0) }),
      ],
      [
        event({
          action: "revoked",
          agent_id: "agent-worker-s7",
          session_id: "s7",
        }),
        event({
          action: "flagged",
          agent_id: "agent-worker-s7",
          secret_ref: SHARED,
        }),
      ],
    );
    const s8 = w.model.byKey.get("s8")!;
    const s9 = w.model.byKey.get("s9")!;
    expect(stillOpenBody(flag(), w.value.incidents, [s8], w.model)).toBe(
      "From the worker-01 incident. A shared key: its owner decides whether to rotate it. worker-02, still running, also saw it.",
    );
    expect(
      stillOpenBody(
        flag({ owner_agent_id: "agent-worker-s9" }),
        w.value.incidents,
        [s8, s9],
        w.model,
      ),
    ).toBe(
      "From the worker-01 incident. It belongs to worker-03. Its owner decides whether to rotate it. worker-02 and 1 more running agents also saw it.",
    );
  });

  it("falls back to the first evidence when no incident in view flagged it", () => {
    const w = world([instance("s8", "active")], []);
    expect(
      stillOpenBody(
        flag({
          evidence: {
            attempted: { source: "egress", agent_id: "agent-worker-s8" },
          },
        }),
        [],
        [],
        w.model,
      ),
    ).toBe(
      "worker-01 tried to send it out; the egress proxy stopped it. A shared key: its owner decides whether to rotate it.",
    );
  });
});
