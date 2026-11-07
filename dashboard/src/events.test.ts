import { describe, expect, it } from "vitest";
import { eventsReducer, initialEventsState, newestEvents } from "./events";
import type { LedgerEvent } from "./contracts";

function event(sequence: number): LedgerEvent {
  return {
    event_id: `evt_${String(sequence).padStart(26, "0")}`,
    ts: new Date().toISOString(),
    agent_id: `agent-${crypto.randomUUID()}`,
    source: "tool_response",
    action: "recorded",
  };
}

describe("events reducer", () => {
  it("deduplicates by event_id, updates repeated events, and sorts newest first", () => {
    const first = event(1);
    const second = event(2);
    const state = eventsReducer(initialEventsState, {
      events: [first, second],
      agents: [],
    });
    const next = eventsReducer(state, {
      events: [{ ...first, detail: "Ledger updated" }],
      agents: [],
    });
    expect(Object.keys(next.byId)).toHaveLength(2);
    expect(newestEvents(next).map((item) => item.event_id)).toEqual([
      second.event_id,
      first.event_id,
    ]);
    expect(next.byId[first.event_id].detail).toBe("Ledger updated");
    expect(state.byId[first.event_id].detail).toBeUndefined();
  });

  it("keeps the cursor through empty polls and never moves backwards", () => {
    const latest = event(3);
    const state = eventsReducer(initialEventsState, {
      events: [latest],
      agents: [],
      cursor: latest.event_id,
    });
    const empty = eventsReducer(state, {
      events: [],
      agents: [],
      cursor: null,
    });
    const older = eventsReducer(empty, {
      events: [event(1)],
      agents: [],
      cursor: event(1).event_id,
    });
    expect(empty.cursor).toBe(latest.event_id);
    expect(older.cursor).toBe(latest.event_id);
  });

  it("accepts the server cursor and authoritative agent updates without events", () => {
    const agent_id = `agent-${crypto.randomUUID()}`;
    const state = eventsReducer(initialEventsState, {
      events: [],
      agents: [{ agent_id, status: "active" }],
      cursor: event(4).event_id,
    });
    const next = eventsReducer(state, {
      events: [],
      agents: [{ agent_id, status: "revoked" }],
    });
    expect(next.agents).toEqual([{ agent_id, status: "revoked" }]);
    expect(next.cursor).toBe(state.cursor);
    expect(eventsReducer(next, { events: [], agents: [] }).agents).toEqual([]);
  });
});
