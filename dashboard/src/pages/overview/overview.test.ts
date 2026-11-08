import { describe, expect, it } from "vitest";
import type {
  AgentState,
  Flag,
  Instance,
  LedgerEvent,
  Role,
} from "../../contracts";
import { mergeFleet } from "../../fleet";
import { buildConsole, burnedToday, incidents } from "../../model";
import type { EventHistory } from "../../polling";
import {
  burnTrigger,
  burnedRows,
  countWord,
  dayStrip,
  decisions,
  evidenceSince,
  heroCopy,
  joinList,
  roleLine,
  runningNowRows,
  sameRow,
  unknownCallers,
} from "./overview";

const NOW = new Date(2026, 10, 8, 20, 0, 0);
const at = (h: number, m: number, s = 0) =>
  new Date(2026, 10, 8, h, m, s).toISOString();
const yesterday = new Date(2026, 10, 7, 12, 0).toISOString();

function instance(
  session: string,
  role: string,
  status: Instance["status"] = "active",
  extra: Partial<Instance> = {},
): Instance {
  return {
    session_id: session,
    agent_id: `agent-${role}-${session}`,
    role,
    status,
    created_at: at(9, Number(session.replace(/\D/g, "")) || 0),
    ...extra,
  };
}

let seq = 0;
function event(fields: Partial<LedgerEvent>): LedgerEvent {
  seq += 1;
  return {
    event_id: fields.event_id ?? `evt_${String(seq).padStart(6, "0")}`,
    ts: at(19, 0),
    agent_id: "system",
    source: "gateway",
    action: "recorded",
    ...fields,
  };
}

/** Newest first, as the console keeps them. */
function newest(list: LedgerEvent[]): LedgerEvent[] {
  return [...list].sort((a, b) => b.event_id.localeCompare(a.event_id));
}

function world({
  instances = [] as Instance[],
  events = [] as LedgerEvent[],
  roles = [] as Role[],
  agents = [] as AgentState[],
  flags = [] as Flag[],
} = {}) {
  const list = newest(events);
  const model = buildConsole({
    fleet: mergeFleet(agents, instances),
    events: list,
    roles,
    now: NOW,
  });
  return {
    model,
    events: list,
    flags,
    burned: burnedToday(model),
    incidents: incidents(model, list, flags),
  };
}

const complete: EventHistory = { from: at(0, 0), complete: true };

const workers = (n: number) =>
  Array.from({ length: n }, (_, i) => instance(`w${i + 1}`, "worker"));

/** worker w3 burned at 7:42 PM by its canary, two keys rotated, one flagged. */
function canaryBurn(extra: Partial<Instance> = {}) {
  return {
    instances: [
      ...workers(2),
      instance("w3", "worker", "revoked", { last_seen: at(19, 41), ...extra }),
      instance("r1", "reader"),
    ],
    events: [
      event({
        event_id: "evt_000100",
        ts: at(19, 42, 9),
        agent_id: "agent-worker-w3",
        session_id: "w3",
        source: "provider",
        action: "canary_used",
        detail: "canary presented (from 203.0.113.9)",
      }),
      event({
        event_id: "evt_000101",
        ts: at(19, 42, 9),
        agent_id: "agent-worker-w3",
        session_id: "w3",
        source: "burn",
        action: "revoked",
        detail: "canary used",
      }),
      ...["api_key", "aws_key"].map((key, i) =>
        event({
          event_id: `evt_00010${2 + i}`,
          ts: at(19, 42, 10),
          agent_id: "agent-worker-w3",
          source: "burn",
          action: "rotated",
          secret_ref: `burn/agent-worker-w3/${key}`,
        }),
      ),
      event({
        event_id: "evt_000104",
        ts: at(19, 42, 10),
        agent_id: "agent-worker-w3",
        source: "burn",
        action: "flagged",
        secret_ref: "burn/shared/crm_db_url",
        flag_level: "reported",
      }),
    ],
    flags: [
      {
        secret_ref: "burn/shared/crm_db_url",
        owner_agent_id: "shared",
        flag_level: "reported",
        instance_count: 1,
        state: "open",
        evidence: {
          reported: { agent_id: "agent-worker-w3", session_id: "w3" },
        },
      } satisfies Flag,
    ],
  };
}

describe("words", () => {
  it("writes small counts as words, as the board does", () => {
    expect(countWord(1)).toBe("one");
    expect(countWord(2)).toBe("two");
    expect(countWord(10)).toBe("ten");
    expect(countWord(11)).toBe("11");
  });

  it("joins a list the way a sentence would", () => {
    expect(joinList(["a"])).toBe("a");
    expect(joinList(["a", "b"])).toBe("a and b");
    expect(joinList(["a", "b", "c"])).toBe("a, b and c");
  });
});

describe("evidence in the stream", () => {
  const since = new Date(NOW.valueOf() - 24 * 3600 * 1000).valueOf();
  it("counts keys seen, credentials blocked at exit and canary uses", () => {
    const counts = evidenceSince(
      [
        event({ action: "recorded", secret_ref: "burn/shared/a" }),
        event({ action: "inherited", secret_ref: "burn/shared/a" }),
        event({ action: "recorded", ts: yesterday.replace("12:", "01:") }),
        event({
          action: "blocked",
          source: "egress",
          secret_ref: "burn/shared/a",
        }),
        // A block with no credential is a policy decision, not a leak.
        event({ action: "blocked", source: "egress", detail: "port" }),
        // A block in a handoff never reached the network.
        event({
          action: "blocked",
          source: "handoff",
          secret_ref: "burn/shared/a",
        }),
        event({ action: "canary_used", source: "provider" }),
      ],
      since,
    );
    expect(counts).toEqual({ keysSeen: 2, blockedAtExit: 1, canaryUses: 1 });
  });

  it("leaves out events older than the window", () => {
    const old = new Date(since - 1).toISOString();
    expect(
      evidenceSince([event({ action: "canary_used", ts: old })], since),
    ).toEqual({ keysSeen: 0, blockedAtExit: 0, canaryUses: 0 });
  });
});

describe("the last 24 hours strip", () => {
  const canary = [event({ action: "canary_used", source: "provider" })];

  it("shows only what the stream can count, with canary uses in ember", () => {
    const strip = dayStrip(canary, complete, NOW);
    expect(strip.figures.map((f) => [f.label, f.value, f.tone])).toEqual([
      ["Keys seen", 0, undefined],
      ["Blocked at exit", 0, undefined],
      ["Canary uses", 1, "ember"],
    ]);
    expect(strip).toMatchObject({ loading: false, lowerBound: false });
  });

  it("waits while today's history is still loading", () => {
    expect(dayStrip(canary, { complete: false }, NOW).loading).toBe(true);
  });

  it("says the figures are lower bounds when history hit its cap", () => {
    const strip = dayStrip(canary, { from: at(15, 15), complete: false }, NOW);
    expect(strip).toMatchObject({
      loading: false,
      lowerBound: true,
      from: at(15, 15),
    });
  });
});

describe("why an agent was burned", () => {
  it("names the canary when the agent's canary was used", () => {
    const { model, events } = world(canaryBurn());
    const agent = model.byKey.get("w3")!;
    expect(burnTrigger(agent, events)).toEqual({
      kind: "canary",
      ip: "203.0.113.9",
    });
  });

  it("names the person and their reason", () => {
    const { model, events } = world({
      instances: [instance("w1", "worker", "revoked")],
      events: [
        event({
          ts: at(19, 0),
          agent_id: "agent-worker-w1",
          session_id: "w1",
          source: "burn",
          action: "revoked",
          actor: "priya@example.com",
          detail: "1 session(s) revoked (read an unverified vendor page)",
        }),
      ],
    });
    expect(burnTrigger(model.byKey.get("w1")!, events)).toEqual({
      kind: "person",
      who: "priya@example.com",
      reason: "read an unverified vendor page",
    });
  });

  it("says when nothing was recorded", () => {
    const { model, events } = world({
      instances: [instance("w1", "worker", "revoked")],
      events: [
        event({
          agent_id: "agent-worker-w1",
          source: "burn",
          action: "revoked",
          detail: "Mock badge revocation.",
        }),
      ],
    });
    expect(burnTrigger(model.byKey.get("w1")!, events)).toEqual({
      kind: "unknown",
    });
  });
});

describe("the hero", () => {
  it("tells one burn the board's way, from the burn's own events", () => {
    const data = world(canaryBurn());
    const hero = heroCopy({ ...data, history: complete });
    expect(hero.incident).toBe(true);
    expect(hero.eyebrow).toBe("Incident 01 / 7:42 PM");
    expect(hero.title).toEqual([
      "worker-03 was burned.",
      "The other 3 kept working.",
    ]);
    expect(hero.lede).toBe(
      "The decoy key planted in worker-03’s session was used from outside Burn, from 203.0.113.9. " +
        "Burn revoked worker-03, rotated the two keys it owned and flagged one shared key for its owner. " +
        "Nothing else restarted.",
    );
    expect(hero.open).toEqual({
      href: "#/agents/w3",
      label: "Open the incident",
    });
    expect(hero.review).toEqual({
      href: "#/credentials",
      label: "Review crm_db_url",
    });
  });

  it("names the replacement that took over", () => {
    const base = canaryBurn({ replaced_by: "w9" });
    const data = world({
      ...base,
      instances: [
        ...base.instances,
        instance("w9", "worker", "active", { created_at: at(19, 43) }),
      ],
    });
    expect(heroCopy({ ...data, history: complete }).lede).toContain(
      "worker-04 took over its task, and nothing else restarted.",
    );
  });

  it("counts several burns together", () => {
    const data = world({
      instances: [
        instance("w1", "worker", "revoked", { last_seen: at(18, 0) }),
        instance("w2", "worker", "revoked", { last_seen: at(19, 0) }),
        instance("w3", "worker"),
      ],
    });
    const hero = heroCopy({ ...data, history: complete });
    expect(hero.eyebrow).toBe("Incident 02");
    expect(hero.title).toEqual([
      "2 agents were burned today.",
      "One other agent kept working.",
    ]);
    expect(hero.lede).toBe(
      "Burn revoked each of them. Nothing else restarted.",
    );
    expect(hero.open).toEqual({
      href: "#/incidents",
      label: "Open the incidents",
    });
  });

  it("reviews a key still open from an earlier burn today", () => {
    const base = canaryBurn();
    const data = world({
      ...base,
      instances: [
        ...base.instances,
        instance("w5", "worker", "revoked", { last_seen: at(19, 50) }),
      ],
    });
    const hero = heroCopy({ ...data, history: complete });
    expect(hero.title[0]).toBe("2 agents were burned today.");
    expect(hero.review).toEqual({
      href: "#/credentials",
      label: "Review crm_db_url",
    });
  });

  it("says all clear with the running count and the region", () => {
    const data = world({ instances: workers(20) });
    const hero = heroCopy({ ...data, history: complete, region: "us-east-1" });
    expect(hero).toMatchObject({
      incident: false,
      eyebrow: "Live / us-east-1",
      title: ["All 20 agents are clean."],
      lede: "No canary uses, blocked leaks or open flags today.",
    });
    expect(hero.open).toBeUndefined();
  });

  it("lists what today did hold when nothing was burned", () => {
    const data = world({
      instances: workers(2),
      events: [
        event({
          action: "blocked",
          source: "egress",
          secret_ref: "burn/shared/a",
        }),
      ],
      flags: [
        {
          secret_ref: "burn/shared/a",
          flag_level: "attempted",
          instance_count: 1,
          state: "open",
        },
      ],
    });
    expect(heroCopy({ ...data, history: complete }).lede).toBe(
      "Today: one leak blocked at exit and one flagged key waiting on its owner. No agent was burned.",
    );
  });

  it("does not claim a clean day it could not fully load", () => {
    const data = world({ instances: workers(2) });
    expect(
      heroCopy({ ...data, history: { from: at(15, 15), complete: false } })
        .lede,
    ).toBe("No canary uses or blocked leaks since 3:15 PM, and no open flags.");
  });

  it("says when nothing is running", () => {
    const hero = heroCopy({ ...world(), history: complete });
    expect(hero.title).toEqual(["No agents are running."]);
  });
});

describe("running now", () => {
  const roles: Role[] = [
    { name: "worker", tools: [], managed_by: "code", owner: "Support" },
    { name: "reader", tools: [], managed_by: "ui" },
    { name: "idle", tools: [], managed_by: "ui" },
  ];

  it("has a row per role with an agent running or burned today, largest first", () => {
    const { model } = world({
      roles,
      instances: [
        instance("r1", "reader"),
        ...workers(2),
        instance("w3", "worker", "revoked", { last_seen: at(19, 41) }),
        instance("w4", "worker", "exited"),
        instance("w5", "worker", "revoked", { last_seen: yesterday }),
      ],
    });
    const rows = runningNowRows(model);
    expect(rows.map((row) => row.name)).toEqual(["worker", "reader"]);
    const [worker] = rows;
    expect(worker).toMatchObject({
      href: "#/roles/worker",
      what: "Support",
      running: 2,
      burnedToday: 1,
    });
    // Finished agents and older burns leave the Overview.
    expect(worker.squares.map((sq) => [sq.label, sq.state, sq.href])).toEqual([
      ["worker-01, running", "running", "#/agents/w1"],
      ["worker-02, running", "running", "#/agents/w2"],
      // Its only time is when it was last seen, which is not a burn time.
      ["worker-03, burned today", "burned", "#/agents/w3"],
    ]);
  });

  it("describes a role by its owner, else where it is managed", () => {
    const { model } = world({ roles, instances: [instance("r1", "reader")] });
    expect(roleLine(model.roleByName.get("worker")!)).toBe("Support");
    expect(roleLine(model.roleByName.get("reader")!)).toBe(
      "Made in the dashboard",
    );
  });

  it("keeps a row's signature while only events change, so it skips a render", () => {
    const first = world({ roles, instances: workers(3) });
    const later = world({
      roles,
      instances: workers(3),
      events: [event({ action: "recorded", agent_id: "agent-worker-w1" })],
    });
    expect(
      sameRow(runningNowRows(first.model)[0], runningNowRows(later.model)[0]),
    ).toBe(true);
    const burned = world({
      roles,
      instances: [...workers(2), instance("w3", "worker", "revoked")],
    });
    expect(
      sameRow(runningNowRows(first.model)[0], runningNowRows(burned.model)[0]),
    ).toBe(false);
  });

  it("stays quick at 200 agents", () => {
    const { model } = world({
      roles,
      instances: Array.from({ length: 200 }, (_, i) =>
        instance(`w${i}`, i % 5 ? "worker" : "reader"),
      ),
    });
    const start = performance.now();
    const rows = runningNowRows(model);
    expect(performance.now() - start).toBeLessThan(50);
    expect(rows.reduce((n, row) => n + row.squares.length, 0)).toBe(200);
  });
});

describe("needs a decision", () => {
  it("lists open flags worst first, with what the evidence shows", () => {
    const { model, events } = world({
      instances: [instance("w1", "worker")],
    });
    const flag = (
      ref: string,
      level: Flag["flag_level"],
      extra: Partial<Flag> = {},
    ): Flag => ({
      secret_ref: ref,
      flag_level: level,
      instance_count: 1,
      state: "open",
      ...extra,
    });
    const items = decisions({
      model,
      events,
      ignored: {},
      flags: [
        flag("burn/shared/seen_key", "seen", {
          evidence: {
            seen: {
              agent_id: "agent-worker-w1",
              session_id: "w1",
              tool: "read_config",
              ts: at(19, 41),
            },
          },
        }),
        flag("burn/shared/crm_db_url", "reported", { instance_count: 3 }),
        flag("burn/shared/done", "seen", { state: "dismissed" }),
      ],
    });
    expect(items.map((item) => [item.tag, item.title, item.hot])).toEqual([
      ["Shared key / reported", "crm_db_url", true],
      ["Shared key / seen", "seen_key", false],
    ]);
    expect(items[0].body).toEqual([
      "An agent was burned holding it. Three agents have seen it. Treat it as leaked.",
    ]);
    expect(items[1].body).toEqual([
      "worker-01 saw it through read_config at 7:41 PM. Nothing tried to move it, and only its owner can rotate it.",
    ]);
  });

  it("groups today's unknown callers by badge client", () => {
    const refused = (client: string, ts: string) =>
      event({
        ts,
        action: "refused",
        detail: `unknown badge client ${client}`,
      });
    const events = newest([
      refused("9c1e", at(19, 31)),
      refused("9c1e", at(19, 31, 20)),
      refused("9c1e", at(19, 31, 40)),
      refused("77aa", at(18, 0)),
      refused("old", yesterday),
    ]);
    expect(
      unknownCallers(events, NOW).map((group) => [group.key, group.count]),
    ).toEqual([
      ["badge:9c1e", 3],
      ["badge:77aa", 1],
    ]);
    const { model } = world();
    const items = decisions({ model, events, flags: [], ignored: {} });
    expect(items[0]).toMatchObject({
      kind: "caller",
      tag: "Unknown caller",
      title: "A badge Burn did not issue",
      body: [
        "Three tool calls from badge client ",
        { id: "9c1e" },
        " at 7:31 PM. All three were refused before any tool ran.",
      ],
    });
  });

  it("hides an ignored caller until it is refused again", () => {
    const first = event({
      event_id: "evt_000900",
      action: "refused",
      detail: "unknown badge client 9c1e",
    });
    const { model } = world();
    const ignored = { "caller:badge:9c1e": "evt_000900" };
    expect(decisions({ model, events: [first], flags: [], ignored })).toEqual(
      [],
    );
    const again = event({
      event_id: "evt_000901",
      action: "refused",
      detail: "unknown badge client 9c1e",
    });
    expect(
      decisions({ model, events: [again, first], flags: [], ignored }),
    ).toHaveLength(1);
  });

  it("puts a canary use on an agent still running first", () => {
    const data = world({
      roles: [
        { name: "worker", tools: [], managed_by: "ui", auto_burn: false },
      ],
      instances: workers(1),
      events: [
        event({
          ts: at(19, 40),
          agent_id: "agent-worker-w1",
          session_id: "w1",
          source: "provider",
          action: "canary_used",
        }),
      ],
      flags: [
        {
          secret_ref: "burn/shared/a",
          flag_level: "reported",
          instance_count: 1,
          state: "open",
        },
      ],
    });
    const items = decisions({ ...data, ignored: {} });
    expect(items.map((item) => item.kind)).toEqual(["canary", "flag"]);
    expect(items[0]).toMatchObject({
      tag: "Canary used / not burned",
      title: "worker-01",
      href: "#/agents/w1",
      body: [
        "Its decoy key was used outside Burn at 7:40 PM. The worker role reports canary uses without burning, so this is yours to decide.",
      ],
    });
  });
});

describe("burned today", () => {
  it("says why and what is still open, never Contained", () => {
    const data = world(canaryBurn());
    expect(burnedRows(data.burned, data.incidents, data.events)).toEqual([
      {
        key: "w3",
        name: "worker-03",
        href: "#/agents/w3",
        why: "worker, 7:42 PM. Its canary was used from outside",
        pill: "1 key open",
        pillTone: "flag",
      },
    ]);
  });

  it("shows a replacement once the keys are settled", () => {
    const data = world({
      ...canaryBurn({
        replacement: {
          status: "linked",
          cutoff: at(19, 42),
          cutoff_source: "evidence",
          done: [],
          quarantined: [],
        },
      }),
      flags: [],
    });
    expect(burnedRows(data.burned, data.incidents, data.events)[0].pill).toBe(
      "Replaced",
    );
  });
});
