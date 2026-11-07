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

export function eventsReducer(
  state: EventsState,
  response: EventsResponse,
): EventsState {
  const byId = { ...state.byId };
  for (const event of response.events) byId[event.event_id] = event;
  return {
    byId,
    agents: response.agents,
    cursor: nextCursor(state.cursor, response),
  };
}

export function newestEvents(state: EventsState): LedgerEvent[] {
  return Object.values(state.byId).sort((a, b) =>
    b.event_id.localeCompare(a.event_id),
  );
}
