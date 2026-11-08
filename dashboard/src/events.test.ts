import { describe, expect, it } from "vitest";
import {
  eventIdAt,
  eventsReducer,
  initialEventsState,
  MAX_EVENTS,
  mergeEvents,
  newestEvents,
} from "./events";
import type { LedgerEvent } from "./contracts";
import { HISTORY_MAX_PAGES, HISTORY_PAGE } from "./polling";

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

describe("event history", () => {
  it("returns the same state for a poll that brings nothing new", () => {
    const first = event(1);
    const state = eventsReducer(initialEventsState, {
      events: [first],
      agents: [],
      cursor: first.event_id,
    });
    expect(eventsReducer(state, { events: [], agents: [], cursor: null })).toBe(
      state,
    );
    expect(eventsReducer(state, { events: [{ ...first }], agents: [] })).toBe(
      state,
    );
    const moved = eventsReducer(state, {
      events: [],
      agents: [{ agent_id: "agent-reader", status: "active" }],
    });
    expect(moved).not.toBe(state);
    expect(moved.byId).toBe(state.byId);
  });

  it("names the first possible event id at a moment, in ULID order", () => {
    // The same ten characters contracts/python/burn_contracts/ids.py writes.
    expect(eventIdAt(1469922850259)).toBe("evt_01ARZ3NDEK0000000000000000");
    expect(eventIdAt(0)).toBe(`evt_${"0".repeat(26)}`);
    expect(eventIdAt(1_000) < eventIdAt(1_001)).toBe(true);
  });

  it("keeps only the newest events once the list is full", () => {
    const state = mergeEvents(
      initialEventsState,
      { events: [event(1), event(2), event(3)], agents: [] },
      2,
    );
    const next = mergeEvents(
      state,
      { events: [event(5), event(4)], agents: [] },
      2,
    );
    expect(newestEvents(state).map((item) => item.event_id)).toEqual([
      event(3).event_id,
      event(2).event_id,
    ]);
    expect(newestEvents(next).map((item) => item.event_id)).toEqual([
      event(5).event_id,
      event(4).event_id,
    ]);
    expect(MAX_EVENTS).toBeGreaterThan(HISTORY_PAGE * HISTORY_MAX_PAGES);
  });
});
