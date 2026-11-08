import { describe, expect, it } from "vitest";
import type { AgentState, Instance, LedgerEvent } from "./contracts";
import {
  containedFleet,
  copyName,
  joinable,
  liveFleet,
  markRevoked,
  mergeFleet,
  roleForAgent,
} from "./fleet";

describe("roles for rebuilding", () => {
  it("matches the longest role name an agent id ends with", () => {
    expect(
      roleForAgent({ agentId: "agent-code-reader" }, ["reader", "code-reader"]),
    ).toBe("code-reader");
    expect(
      roleForAgent({ agentId: "agent-x", role: "worker" }, ["worker"]),
    ).toBe("worker");
    expect(roleForAgent({ agentId: "agent-x" }, ["reader"])).toBeUndefined();
  });

  it("names a copy within 32 characters and never reuses a name", () => {
    expect(copyName("reader", [])).toBe("reader-2");
    expect(copyName("reader", ["reader-2"])).toBe("reader-3");
    const long = "x".repeat(32);
    expect(copyName(long, [])).toHaveLength(32);
    expect(copyName("abc-".padEnd(32, "-"), [])).toBe("abc-2");
  });

  it("does not mint join tokens for scenario roles", () => {
    expect(joinable({ managed_by: "scenario" })).toBe(false);
    expect(joinable({ managed_by: "ui" })).toBe(true);
    expect(joinable({ managed_by: "code" })).toBe(true);
  });
});

function instance(fields: Partial<Instance>): Instance {
  return {
    session_id: "sess_1",
    agent_id: "agent-reader",
    role: "reader",
    status: "active",
    tools: [],
    owner: "support",
    created_at: "2026-11-08T05:00:00.000Z",
    exposures: 2,
    ...fields,
  } as Instance;
}

function agent(fields: Partial<AgentState>): AgentState {
  return {
    agent_id: "agent-reader",
    role: "reader",
    status: "active",
    session_id: "sess_1",
    exposures: 2,
    ...fields,
  };
}

describe("mergeFleet", () => {
  it("takes a burn from the event feed before /instances catches up", () => {
    const fleet = mergeFleet(
      [agent({ status: "revoked" })],
      [instance({ status: "active" })],
    );
    expect(fleet).toHaveLength(1);
    expect(fleet[0].status).toBe("revoked");
    // The rest of the entry still comes from /instances.
    expect(fleet[0].owner).toBe("support");
    expect(fleet[0].createdAt).toBe("2026-11-08T05:00:00.000Z");
    expect(liveFleet(fleet)).toEqual([]);
  });

  it("never moves an ended session back to active", () => {
    const fleet = mergeFleet(
      [agent({ status: "active" })],
      [instance({ status: "revoked" })],
    );
    expect(fleet[0].status).toBe("revoked");
  });

  it("keeps one entry per session", () => {
    const fleet = mergeFleet(
      [agent({ session_id: "sess_2" })],
      [
        instance({ session_id: "sess_1", status: "revoked" }),
        instance({ session_id: "sess_2" }),
      ],
    );
    expect(fleet.map((entry) => [entry.key, entry.status])).toEqual([
      ["sess_1", "revoked"],
      ["sess_2", "active"],
    ]);
  });
});

describe("markRevoked", () => {
  it("marks only the sessions a burn revoked", () => {
    const fleet = mergeFleet(
      [],
      [
        instance({ session_id: "sess_1", agent_id: "agent-swarm-a1" }),
        instance({ session_id: "sess_2", agent_id: "agent-swarm-b2" }),
      ],
    );
    const marked = markRevoked(fleet, new Set(["sess_2"]));
    expect(marked.map((entry) => entry.status)).toEqual(["active", "revoked"]);
    expect(markRevoked(fleet, new Set())).toBe(fleet);
  });
});

describe("containedFleet", () => {
  function revoked(session: string | null, ts: string, id: string) {
    return {
      event_id: id,
      ts,
      agent_id: "agent-reader",
      session_id: session,
      source: "burn",
      action: "revoked",
      detail: `burned ${id}`,
    } as LedgerEvent;
  }

  it("gives each session its own burn, and none to a session no burn ended", () => {
    const fleet = mergeFleet(
      [],
      [
        instance({ session_id: "sess_1", status: "revoked" }),
        instance({ session_id: "sess_2", status: "revoked" }),
        // Ended when the agent was restarted, not by a burn.
        instance({ session_id: "sess_3", status: "revoked" }),
      ],
    );
    const contained = containedFleet(fleet, [
      revoked("sess_1", "2026-11-08T05:01:00.000Z", "evt_01"),
      revoked("sess_2", "2026-11-08T05:02:00.000Z", "evt_02"),
    ]);
    const bySession = Object.fromEntries(
      contained.map((item) => [item.entry.sessionId, item]),
    );
    expect(bySession.sess_1.revokedAt).toBe("2026-11-08T05:01:00.000Z");
    expect(bySession.sess_2.revokedAt).toBe("2026-11-08T05:02:00.000Z");
    expect(bySession.sess_2.note).toBe("burned evt_02");
    expect(bySession.sess_3.revokedAt).toBeUndefined();
    expect(bySession.sess_3.note).toBeUndefined();
  });

  it("matches an event that names no session by agent id, only when that agent has one burned session", () => {
    const one = mergeFleet(
      [],
      [instance({ session_id: "sess_1", status: "revoked" })],
    );
    const event = revoked(null, "2026-11-08T05:03:00.000Z", "evt_03");
    expect(containedFleet(one, [event])[0].revokedAt).toBe(
      "2026-11-08T05:03:00.000Z",
    );
    const two = mergeFleet(
      [],
      [
        instance({ session_id: "sess_1", status: "revoked" }),
        instance({ session_id: "sess_2", status: "revoked" }),
      ],
    );
    expect(containedFleet(two, [event]).map((item) => item.revokedAt)).toEqual([
      undefined,
      undefined,
    ]);
  });
});
