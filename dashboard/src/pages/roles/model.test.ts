import { describe, expect, it } from "vitest";
import type {
  EnrollToken,
  Instance,
  LedgerEntry,
  LedgerEvent,
  Role,
} from "../../contracts";
import { mergeFleet } from "../../fleet";
import { buildConsole, roleCounts } from "../../model";
import {
  accessLevel,
  FULL_ACCESS_TOOLS,
  filterOf,
  keysSeen,
  keysText,
  lastCall,
  lastCallText,
  lastCalls,
  managedIn,
  protections,
  reach,
  roleRows,
  rolePlural,
  roleTokens,
  rolesList,
  sharedKeys,
  startedBy,
  tokenLine,
  tokenOpen,
  type Filter,
  type TokenRow,
} from "./model";

// Local times, so "today" is the same day wherever the tests run.
const now = new Date(2026, 10, 8, 19, 45);
const at = (h: number, m: number, s = 0) =>
  new Date(2026, 10, 8, h, m, s).toISOString();

function instance(
  session: string,
  role: string,
  status: Instance["status"],
  created: string,
  extra: Partial<Instance> = {},
): Instance {
  return {
    session_id: session,
    agent_id: `agent-${role}-${session}`,
    role,
    status,
    created_at: created,
    ...extra,
  };
}

function modelOf(instances: Instance[], events: LedgerEvent[] = []) {
  return buildConsole({ fleet: mergeFleet([], instances), events, now });
}

// worker-01 finished; worker-02 burned and started a reader; worker-03 is
// running and started worker-04. reader-01 is nobody's child.
const fleet = [
  instance("w1", "worker", "exited", at(19, 0)),
  instance("w2", "worker", "revoked", at(19, 1)),
  instance("w3", "worker", "active", at(19, 2), { created_by: "p@x.test" }),
  instance("w4", "worker", "active", at(19, 3), { parent_session: "w3" }),
  instance("r1", "reader", "active", at(19, 4), { parent_session: "w2" }),
  instance("r2", "reader", "active", at(19, 0)),
];
const model = modelOf(fleet);
const rows = (filter: Filter, role = "worker") =>
  roleRows(model, role, filter).map(
    (row) => `${"  ".repeat(row.depth)}${row.agent.name}`,
  );

describe("roleRows", () => {
  it("lists running agents, with a child under its parent while the parent is shown", () => {
    expect(rows("running")).toEqual(["reader-02", "worker-03", "  worker-04"]);
  });

  it("keeps another role's child under its burned parent", () => {
    expect(rows("burned")).toEqual(["worker-02", "  reader-02"]);
    expect(rows("finished")).toEqual(["worker-01"]);
    expect(rows("all")).toEqual([
      "worker-01",
      "worker-02",
      "  reader-02",
      "worker-03",
      "  worker-04",
    ]);
  });

  it("counts only the role's own agents, whatever rows it shows", () => {
    expect(roleCounts(model, "worker")).toMatchObject({
      running: 2,
      burned: 1,
      finished: 1,
      agents: 4,
    });
    // reader-02 still counts as a reader, and is listed there on its own.
    expect(rows("running", "reader")).toEqual(["reader-01", "reader-02"]);
  });

  it("shows every agent once through a cycle or a parent the API no longer lists", () => {
    const odd = modelOf([
      instance("a", "worker", "active", at(19, 0), { parent_session: "b" }),
      instance("b", "worker", "active", at(19, 1), { parent_session: "a" }),
      instance("c", "worker", "active", at(19, 2), { parent_session: "gone" }),
    ]);
    const shown = roleRows(odd, "worker", "all");
    expect(shown.map((row) => row.agent.key).sort()).toEqual(["a", "b", "c"]);
    expect(shown.find((row) => row.agent.key === "c")?.depth).toBe(0);
  });
});

describe("startedBy", () => {
  const line = (filter: Filter, key: string, from = model) =>
    startedBy(
      roleRows(from, "worker", filter).find((row) => row.agent.key === key)!,
      from,
    );

  it("names a child's parent, with its state when the parent isn't the row above", () => {
    expect(line("running", "r1")).toBe(
      "A reader spawned by worker-02 (burned)",
    );
    expect(line("burned", "r1")).toBe("A reader spawned by worker-02");
    expect(line("running", "w4")).toBe("Spawned by worker-03");
  });

  it("names the person, the replaced agent, the scenario or the token", () => {
    expect(line("running", "w3")).toBe("p@x.test");
    expect(line("finished", "w1")).toBe("");
    const other = modelOf([
      instance("old", "worker", "revoked", at(19, 0)),
      instance("new", "worker", "active", at(19, 5), { replaces: "old" }),
      instance("tok", "worker", "active", at(19, 6), {
        token_id: "7ff596f9431cd7eb",
      }),
      instance("orphan", "worker", "active", at(19, 7), {
        parent_session: "gone",
      }),
    ]);
    expect(line("running", "new", other)).toBe("Replaces worker-01");
    expect(line("running", "tok", other)).toBe("Joined with token 7ff596f9");
    expect(line("running", "orphan", other)).toBe(
      "Spawned by an agent Burn no longer lists",
    );
    const scenario = buildConsole({
      fleet: mergeFleet(
        [{ agent_id: "agent-alpha", role: "alpha", status: "idle" }],
        [],
      ),
      events: [],
      now,
    });
    expect(startedBy(roleRows(scenario, "alpha", "running")[0], scenario)).toBe(
      "Started by the scenario",
    );
  });
});

describe("last call", () => {
  const event = (
    ts: string,
    source: LedgerEvent["source"],
    session = "w3",
  ): LedgerEvent => ({
    event_id: `evt_${ts}`,
    ts,
    agent_id: "agent-worker-w3",
    session_id: session,
    source,
    action: "recorded",
  });

  it("takes each agent's newest call from newest-first events, ignoring Burn's own", () => {
    const calls = lastCalls([
      event(at(19, 45), "burn"),
      event(at(19, 44), "gateway"),
      event(at(19, 40), "tool_response"),
    ]);
    expect(calls.get("w3")).toBe(at(19, 44));
  });

  it("is the later of that call and the API's last_seen", () => {
    const agent = model.byKey.get("w3")!;
    const calls = new Map([["w3", at(19, 44)]]);
    expect(lastCall({ ...agent, lastSeen: at(19, 42) }, calls)).toBe(
      at(19, 44),
    );
    expect(
      lastCall({ ...agent, lastSeen: "2026-11-09T03:44:30Z" }, calls),
    ).toBe("2026-11-09T03:44:30Z");
    expect(lastCall({ ...agent, lastSeen: null }, new Map())).toBeUndefined();
  });

  it("reads as time ago while running and a quiet clock time after", () => {
    expect(lastCallText("running", at(19, 44, 58), now)).toEqual({
      text: "2 s ago",
      quiet: false,
    });
    expect(lastCallText("burned", at(19, 42), now)).toEqual({
      text: "7:42 PM",
      quiet: true,
    });
    expect(lastCallText("running", undefined, now)).toEqual({
      text: "Not reported",
      quiet: true,
    });
  });
});

describe("keys it saw", () => {
  const entry = (exposure?: "seen" | "referenced"): LedgerEntry => ({
    secret_ref: "burn/shared/crm_db_url",
    fingerprint: "f",
    secret_type: "other",
    owner: "shared",
    source: "tool_response",
    first_seen: at(19, 0),
    status: "active",
    ...(exposure ? { exposure } : {}),
  });

  it("counts plain text and references, a missing exposure as plain text", () => {
    expect(
      keysSeen([
        entry("referenced"),
        entry("referenced"),
        entry("referenced"),
        entry("seen"),
        entry(),
      ]),
    ).toEqual({ plain: 2, referenced: 3 });
  });

  it("says it in a sentence, plain text marked hot", () => {
    expect(keysText({ plain: 1, referenced: 3 }, 4, "running")).toEqual({
      text: "1 in plain text, 3 references",
      hot: true,
      quiet: false,
    });
    expect(keysText({ plain: 0, referenced: 1 }, 1, "running").text).toBe(
      "1 reference",
    );
    expect(keysText({ plain: 0, referenced: 0 }, 0, "running").text).toBe(
      "Nothing yet",
    );
    expect(keysText({ plain: 0, referenced: 0 }, 0, "finished").text).toBe(
      "Nothing",
    );
  });

  it("falls back to the key count, quietly, until the ledger loads", () => {
    expect(keysText(undefined, 3, "running")).toEqual({
      text: "3 keys",
      hot: false,
      quiet: true,
    });
    expect(keysText(undefined, 0, "running").text).toBe("Nothing yet");
    expect(keysText(undefined, undefined, "running").text).toBe("");
  });
});

describe("the role's records", () => {
  const role: Role = { name: "worker", tools: [], managed_by: "code" };

  it("says where a role is managed", () => {
    expect(managedIn(role)).toBe("burn.yaml");
    expect(managedIn({ ...role, managed_by: "ui" })).toBe("this dashboard");
    expect(managedIn({ ...role, managed_by: "scenario" })).toBe("the scenario");
    expect(managedIn(undefined)).toBe("");
  });

  it("reads protections with the API's defaults", () => {
    expect(protections(role).map((row) => [row.label, row.value])).toEqual([
      ["Keys as references", "On"],
      ["Canary in every session", "On"],
      ["Burn on canary use", "On"],
      ["Replace a burned worker", "Off"],
    ]);
    const changed = protections({
      ...role,
      references: "report",
      canaries: false,
      replace_on_burn: true,
    });
    expect(changed.map((row) => row.value)).toEqual([
      "Report only",
      "Off",
      "Off",
      "On",
    ]);
    expect(changed[2].note).toBe("No canary to use");
    expect(protections({ ...role, auto_burn: false })[2].value).toBe("Off");
    expect(protections({ ...role, references: "off" })[0]).toMatchObject({
      value: "Off",
      note: "Agents see the value",
    });
  });

  it("says where its agents can reach", () => {
    expect(reach(null)).toEqual({
      hosts: [],
      text: "The default allowlist and Burn's Gateway.",
    });
    expect(reach([]).text).toBe("Only Burn's Gateway. Nothing else.");
    expect(reach(["api.example.com"])).toEqual({
      hosts: ["api.example.com"],
      text: ", Burn's Gateway. Nothing else.",
    });
  });

  it("lists the shared keys its agents were seen holding", () => {
    const seen = (
      agent: string,
      ref: string,
      owner: LedgerEvent["owner"],
      action: LedgerEvent["action"] = "recorded",
    ): LedgerEvent => ({
      event_id: `evt_${agent}${ref}${action}`,
      ts: at(19, 0),
      agent_id: agent,
      source: "tool_response",
      action,
      secret_ref: ref,
      owner,
    });
    expect(
      sharedKeys(
        [
          seen("agent-worker-w3", "burn/shared/crm_db_url", "shared"),
          seen("agent-worker-w3", "burn/shared/crm_db_url", "shared"),
          seen("agent-worker-w2", "burn/shared/billing", "shared", "flagged"),
          seen("agent-worker-w3", "burn/agent-worker-w3/api_key", "agent"),
          seen("agent-reader-r2", "burn/shared/zendesk", "shared"),
        ],
        new Set(["agent-worker-w2", "agent-worker-w3"]),
      ),
    ).toEqual(["crm_db_url"]);
  });

  it("pluralises role names for its copy", () => {
    expect(rolePlural("worker")).toBe("workers");
    expect(rolePlural("analysis")).toBe("analyses");
    expect(rolePlural("registry")).toBe("registries");
    expect(rolePlural("relay")).toBe("relays");
    expect(rolePlural("inbox")).toBe("inboxes");
    expect(rolePlural("")).toBe("agents");
  });

  it("starts on Running and keeps a known filter from the address", () => {
    expect(filterOf(undefined)).toBe("running");
    expect(filterOf("burned")).toBe("burned");
    expect(filterOf("instances")).toBe("running");
  });
});

describe("join tokens", () => {
  const TOKEN = "7ff596f9431cd7eb";
  const tokens = modelOf([
    instance("t1", "worker", "active", at(19, 10), { token_id: TOKEN }),
    instance("t2", "worker", "exited", at(19, 11), { token_id: TOKEN }),
    instance("t3", "worker", "active", at(19, 12), {
      parent_session: "t1",
    }),
  ]);
  const meta = (extra: Partial<EnrollToken> = {}): EnrollToken => ({
    token_id: "abc",
    role: "worker",
    uses: 5,
    uses_left: 5,
    expires_at: at(21, 10),
    status: "active",
    created_at: at(19, 10),
    created_by: "p@x.test",
    ...extra,
  });

  it("lists the tokens its agents joined with, and the ones made here, newest first", () => {
    const list = roleTokens(
      tokens.agents,
      [
        meta({ created_at: at(19, 30) }),
        meta({ token_id: "x", role: "reader" }),
      ],
      "worker",
    );
    expect(list.map((row) => [row.tokenId, row.joined])).toEqual([
      ["abc", 0],
      [TOKEN, 2],
    ]);
    expect(list[1].firstJoin).toBe(at(19, 10));
  });

  it("says what is known of each token", () => {
    const row = (extra: Partial<TokenRow> = {}): TokenRow => ({
      tokenId: "abc",
      joined: 0,
      ...extra,
    });
    expect(
      tokenLine(row({ joined: 2, firstJoin: at(19, 10) }), "worker", now),
    ).toBe("2 workers joined with it, first at 7:10 PM.");
    expect(tokenLine(row({ meta: meta() }), "worker", now)).toBe(
      "p@x.test, 7:10 PM. 5 of 5 uses left, expires 9:10 PM.",
    );
    expect(tokenLine(row({ meta: meta(), joined: 2 }), "worker", now)).toBe(
      "p@x.test, 7:10 PM. 3 of 5 uses left, expires 9:10 PM. 2 workers joined with it.",
    );
    expect(
      tokenLine(
        row({ meta: meta({ status: "revoked" }), joined: 1 }),
        "worker",
        now,
      ),
    ).toBe("p@x.test, 7:10 PM. Revoked. 1 worker joined with it.");
    expect(
      tokenLine(row({ meta: meta({ expires_at: at(19, 0) }) }), "worker", now),
    ).toBe("p@x.test, 7:10 PM. Expired 7:00 PM.");
    expect(
      tokenLine(row({ meta: meta({ uses: 2 }), joined: 2 }), "worker", now),
    ).toBe("p@x.test, 7:10 PM. All 2 uses taken. 2 workers joined with it.");
  });

  it("offers Revoke unless the token is known to be closed", () => {
    expect(tokenOpen({ tokenId: "a", joined: 3 }, now)).toBe(true);
    expect(tokenOpen({ tokenId: "a", joined: 0, meta: meta() }, now)).toBe(
      true,
    );
    expect(
      tokenOpen(
        { tokenId: "a", joined: 0, meta: meta({ status: "revoked" }) },
        now,
      ),
    ).toBe(false);
    expect(
      tokenOpen(
        { tokenId: "a", joined: 0, meta: meta({ expires_at: at(19, 0) }) },
        now,
      ),
    ).toBe(false);
  });
});

describe("rolesList", () => {
  const roles: Role[] = [
    {
      name: "worker",
      tools: [],
      owner: "Support automation",
      managed_by: "code",
    },
    { name: "triage", tools: [...FULL_ACCESS_TOOLS], managed_by: "ui" },
    { name: "reader", tools: [], managed_by: "code" },
  ];
  // worker-02 was last seen, so burned, at 7:30 PM today.
  const today = fleet.map((agent) =>
    agent.session_id === "w2" ? { ...agent, last_seen: at(19, 30) } : agent,
  );
  const list = rolesList(
    buildConsole({ fleet: mergeFleet([], today), events: [], roles, now }),
  );

  const row = (name: string) => list.find((found) => found.name === name)!;

  it("lists every role, most running first, then most burned today", () => {
    expect(list.map((found) => found.name)).toEqual([
      "worker",
      "reader",
      "triage",
    ]);
  });

  it("says whether a role has Full access, who owns it and where it is managed", () => {
    expect(list.map((found) => found.line)).toEqual([
      "Limited · Support automation · managed in burn.yaml",
      "Limited · managed in burn.yaml",
      "Full access · managed in this dashboard",
    ]);
  });

  it("draws a square per running agent and per agent burned today", () => {
    expect(row("worker").squares.map((square) => square.label)).toEqual([
      "worker-02, burned today",
      "worker-03, running",
      "worker-04, running",
    ]);
    expect(row("worker")).toMatchObject({
      running: 2,
      note: "1 burned today",
      hot: true,
    });
  });

  it("notes what is left when nothing burned today", () => {
    expect(row("reader")).toMatchObject({
      running: 2,
      note: "Nothing burned today",
      hot: false,
    });
    expect(row("triage")).toMatchObject({
      running: 0,
      squares: [],
      note: "No agents yet",
    });
    const finished = rolesList(
      modelOf([instance("w1", "worker", "exited", at(19, 0))]),
    );
    expect(finished[0].note).toBe("1 finished");
  });
});

describe("accessLevel", () => {
  it("is Full access with every tool and no egress list", () => {
    expect(accessLevel({ tools: [...FULL_ACCESS_TOOLS], egress: null })).toBe(
      "Full access",
    );
    expect(
      accessLevel({
        tools: [...FULL_ACCESS_TOOLS].reverse(),
        egress: undefined,
      }),
    ).toBe("Full access");
  });

  it("is Limited with a tool missing or any egress list", () => {
    expect(
      accessLevel({ tools: FULL_ACCESS_TOOLS.slice(1), egress: null }),
    ).toBe("Limited");
    expect(accessLevel({ tools: [...FULL_ACCESS_TOOLS], egress: [] })).toBe(
      "Limited",
    );
    expect(
      accessLevel({
        tools: [...FULL_ACCESS_TOOLS],
        egress: ["api.example.com"],
      }),
    ).toBe("Limited");
  });
});
