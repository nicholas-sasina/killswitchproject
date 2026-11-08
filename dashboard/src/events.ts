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
 * Merge one poll into the state. A poll that brings nothing new returns the
 * same state object, so nothing downstream re-renders every second.
 */
export function eventsReducer(
  state: EventsState,
  response: EventsResponse,
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
  if (changed)
    for (const event of response.events) byId[event.event_id] = event;
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
