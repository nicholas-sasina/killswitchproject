import { describe, expect, it } from "vitest";
import type { Instance, LedgerEvent } from "../contracts";
import { mergeFleet } from "../fleet";
import {
  agentForEvent,
  buildConsole,
  burnedToday,
  findAgent,
  roleCounts,
} from "./console";

// Local times, so "today" is the same day wherever the tests run.
const now = new Date(2026, 10, 8, 19, 45);
const at = (h: number, m: number, day = 8) =>
  new Date(2026, 10, day, h, m).toISOString();

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

function revoked(session: string, role: string, ts: string): LedgerEvent {
  return {
    event_id: `evt_${ts}`,
    ts,
    agent_id: `agent-${role}-${session}`,
    session_id: session,
    source: "burn",
    action: "revoked",
    detail: "1 session(s) revoked (read a poisoned ticket)",
  };
}

const instances = [
  instance("w1", "worker", "active", at(19, 10)),
  instance("w2", "worker", "revoked", at(19, 11), { last_seen: at(19, 41) }),
  instance("w3", "worker", "active", at(19, 12)),
  instance("w0", "worker", "exited", at(18, 0)),
  instance("w4", "worker", "revoked", at(9, 0, 7), { last_seen: at(9, 30, 7) }),
  instance("r1", "reader", "active", at(19, 18), { parent_session: "w2" }),
];
const events = [revoked("w2", "worker", at(19, 42))];

describe("the console model", () => {
  const model = buildConsole({
    fleet: mergeFleet([], instances),
    events,
    roles: [
      { name: "worker", tools: [], managed_by: "code" },
      { name: "notifier", tools: [], managed_by: "ui" },
    ],
    now,
  });

  it("names agents and calls their states running, burned or finished", () => {
    expect(model.agents.map((agent) => [agent.name, agent.state])).toEqual([
      ["reader-01", "running"],
      ["worker-01", "burned"],
      ["worker-02", "finished"],
      ["worker-03", "running"],
      ["worker-04", "burned"],
      ["worker-05", "running"],
    ]);
  });

  it("scopes burned today to burns on this calendar day, timed by their event", () => {
    const today = burnedToday(model);
    expect(today.map((agent) => agent.key)).toEqual(["w2"]);
    expect(today[0].burnedAt).toBe(at(19, 42));
    expect(today[0].burnedAtFrom).toBe("event");
    expect(today[0].burnNote).toBe(
      "1 session(s) revoked (read a poisoned ticket)",
    );
    // Yesterday's burn has no event loaded: its last heartbeat dates it.
    expect(model.byKey.get("w4")?.burnedAtFrom).toBe("last-seen");
    expect(model.byKey.get("w4")?.burnedToday).toBe(false);
  });

  it("groups agents by role, largest first, with roles nobody runs last", () => {
    expect(model.roles.map((role) => role.name)).toEqual([
      "worker",
      "reader",
      "notifier",
    ]);
    const worker = model.roleByName.get("worker");
    expect(worker?.role?.managed_by).toBe("code");
    expect(worker?.running.map((agent) => agent.name)).toEqual([
      "worker-03",
      "worker-05",
    ]);
    // worker-01 was burned yesterday, so only worker-04 counts today.
    expect(worker?.burnedToday.map((agent) => agent.name)).toEqual([
      "worker-04",
    ]);
  });

  it("computes every count from the same agents", () => {
    expect(model.counts).toEqual({
      running: 3,
      burned: 2,
      burnedToday: 1,
      finished: 1,
      agents: 6,
      rolesRunning: 2,
      rolesNow: 2,
      roles: 3,
    });
    const perRole = model.roles.map((role) => roleCounts(model, role.name));
    const sum = (field: "running" | "burnedToday" | "agents") =>
      perRole.reduce((total, counts) => total + counts[field], 0);
    expect(sum("running")).toBe(model.counts.running);
    expect(sum("burnedToday")).toBe(model.counts.burnedToday);
    expect(sum("agents")).toBe(model.counts.agents);
  });

  it("links a child to its parent across roles", () => {
    const reader = model.byKey.get("r1");
    expect(reader?.parent).toBe("w2");
    expect(model.byKey.get("w2")?.children).toEqual(["r1"]);
  });

  it("finds an agent by session first, then by agent id", () => {
    expect(findAgent(model, "w3")?.name).toBe("worker-05");
    expect(findAgent(model, "agent-worker-w3")?.name).toBe("worker-05");
    expect(findAgent(model, "nobody")).toBeUndefined();
    expect(
      agentForEvent(model, { agent_id: "agent-reader-r1", session_id: null })
        ?.name,
    ).toBe("reader-01");
  });

  it("includes a scenario agent that has no instance record", () => {
    const scenario = buildConsole({
      fleet: mergeFleet(
        [{ agent_id: "agent-reader", status: "active", session_id: "s1" }],
        [],
      ),
      events: [],
      now,
    });
    expect(scenario.agents.map((agent) => [agent.name, agent.from])).toEqual([
      ["reader-01", "scenario"],
    ]);
  });
});
