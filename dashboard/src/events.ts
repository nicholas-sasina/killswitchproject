import type { AgentState, EventsResponse, LedgerEvent } from "./contracts";

export interface EventsState {
  byId: Record<string, LedgerEvent>;
  agents: AgentState[];
  cursor?: string;
}

export const initialEventsState: EventsState = { byId: {}, agents: [] };

export function nextCursor(
  current: string | undefined,
  response: EventsResponse,
): string | undefined {
  return [
    current,
    response.cursor,
    ...response.events.map((event) => event.event_id),
  ]
    .filter((id): id is string => Boolean(id))
    .sort()
    .at(-1);
}

/**
 * The most events kept. A day's history (at most 20,000 events) fits with
 * room to spare; past that the oldest are dropped, so a console left open
 * for days on a busy swarm stays the same size.
 */
export const MAX_EVENTS = 25_000;

/**
 * Merge one poll into the state, keeping at most `MAX_EVENTS`. A poll that
 * brings nothing new returns the same state object, so nothing downstream
 * re-renders every second.
 */
export function eventsReducer(
  state: EventsState,
  response: EventsResponse,
): EventsState {
  return mergeEvents(state, response, MAX_EVENTS);
}

/** `eventsReducer` with a chosen limit on how many events are kept. */
export function mergeEvents(
  state: EventsState,
  response: EventsResponse,
  limit: number,
): EventsState {
  const cursor = nextCursor(state.cursor, response);
  const changed = response.events.some(
    (event) =>
      JSON.stringify(state.byId[event.event_id]) !== JSON.stringify(event),
  );
  if (
    !changed &&
    cursor === state.cursor &&
    JSON.stringify(response.agents) === JSON.stringify(state.agents)
  )
    return state;
  const byId = changed ? { ...state.byId } : state.byId;
  if (changed) {
    for (const event of response.events) byId[event.event_id] = event;
    const ids = Object.keys(byId);
    if (ids.length > limit)
      for (const id of ids.sort().slice(0, ids.length - limit)) delete byId[id];
  }
  return { byId, agents: response.agents, cursor };
}

// Crockford base32, the ULID alphabet.
const ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/**
 * The smallest event id at a moment. Event ids are "evt_" plus a ULID, whose
 * first ten characters encode the millisecond, so `since=` this id asks the
 * API for every event from that moment on.
 */
export function eventIdAt(ms: number): string {
  let value = Math.max(0, Math.floor(ms));
  let time = "";
  for (let i = 0; i < 10; i += 1) {
    time = ULID_ALPHABET[value % 32] + time;
    value = Math.floor(value / 32);
  }
  return `evt_${time}${"0".repeat(16)}`;
}

export function newestEvents(state: EventsState): LedgerEvent[] {
  return Object.values(state.byId).sort((a, b) =>
    b.event_id.localeCompare(a.event_id),
  );
}
