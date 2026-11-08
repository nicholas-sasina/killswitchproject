import { describe, expect, it } from "vitest";
import type { Instance, LedgerEvent } from "../contracts";
import { mergeFleet } from "../fleet";
import { burnTrigger, triggerShort } from "./burns";
import { buildConsole } from "./console";

const now = new Date(2026, 10, 8, 20, 0);
const ts = (m: number) => new Date(2026, 10, 8, 19, m).toISOString();

const burned: Instance = {
  session_id: "w1",
  agent_id: "agent-worker-w1",
  role: "worker",
  status: "revoked",
  created_at: ts(0),
};

function event(fields: Partial<LedgerEvent>): LedgerEvent {
  return {
    event_id: `evt_${fields.action}`,
    ts: ts(42),
    agent_id: "agent-worker-w1",
    session_id: "w1",
    source: "burn",
    action: "revoked",
    ...fields,
  };
}

function triggerOf(events: LedgerEvent[]) {
  const model = buildConsole({
    fleet: mergeFleet([], [burned]),
    events,
    now,
  });
  return burnTrigger(model.byKey.get("w1")!, events);
}

describe("why an agent was burned", () => {
  it("names the canary when the agent's canary was used", () => {
    const trigger = triggerOf([
      event({
        source: "provider",
        action: "canary_used",
        detail: "canary presented (from 203.0.113.9)",
      }),
      event({ detail: "1 session(s) revoked" }),
    ]);
    expect(trigger).toEqual({ kind: "canary", ip: "203.0.113.9" });
    expect(triggerShort(trigger)).toBe("Its canary was used from outside");
  });

  it("names the person and their reason", () => {
    const trigger = triggerOf([
      event({
        actor: "priya@example.com",
        detail: "1 session(s) revoked (read an unverified vendor page)",
      }),
    ]);
    expect(trigger).toEqual({
      kind: "person",
      who: "priya@example.com",
      reason: "read an unverified vendor page",
    });
    expect(triggerShort(trigger)).toBe(
      "Burned by priya@example.com: read an unverified vendor page",
    );
  });

  it("gives the reason when no person is named", () => {
    const trigger = triggerOf([
      event({ actor: "system", detail: "1 session(s) revoked (auto-burn)" }),
    ]);
    expect(trigger).toEqual({ kind: "reason", reason: "auto-burn" });
    expect(triggerShort(trigger)).toBe("Burned: auto-burn");
  });

  it("never names an agent's own badge as the person who burned it", () => {
    const trigger = triggerOf([
      event({
        actor: "instance:session_0123",
        detail: "1 session(s) revoked (child misbehaved)",
      }),
    ]);
    expect(trigger).toEqual({ kind: "reason", reason: "child misbehaved" });
  });

  it("says when nothing was recorded, and ignores another agent's burn", () => {
    const trigger = triggerOf([
      event({ detail: "Mock badge revocation.", session_id: null }),
      event({
        agent_id: "agent-worker-w2",
        session_id: "w2",
        actor: "priya@example.com",
        detail: "1 session(s) revoked (not this one)",
      }),
    ]);
    expect(trigger).toEqual({ kind: "unknown" });
    expect(triggerShort(trigger)).toBe("No reason was recorded");
  });
});
