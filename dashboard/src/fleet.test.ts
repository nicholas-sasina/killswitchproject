import { describe, expect, it } from "vitest";
import type { AgentState, Instance } from "./contracts";
import { liveFleet, markRevoked, mergeFleet } from "./fleet";

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
