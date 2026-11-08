/**
 * Which agents made a call in the latest poll, and a way to tell their
 * squares without re-rendering the page. A square dims briefly only when the
 * event stream shows its agent did something: never on a timer, never at
 * random.
 */
import type { LedgerEvent } from "../contracts";

/** Event sources that mean an agent made a call. */
const CALL_SOURCES = new Set<LedgerEvent["source"]>([
  "tool_response",
  "gateway",
  "handoff",
  "egress",
]);

/**
 * The agents (by session, or agent id with no session) that made a call in
 * events newer than `after`. With no `after` (the first poll), none: the
 * page's first paint shows the fleet still.
 */
export function callersSince(
  events: Iterable<LedgerEvent>,
  after: string | undefined,
): Set<string> {
  const callers = new Set<string>();
  if (after === undefined) return callers;
  for (const event of events)
    if (event.event_id > after && CALL_SOURCES.has(event.source))
      callers.add(event.session_id || event.agent_id);
  return callers;
}

export type PulseListener = () => void;

/** A small subscribe-by-key bus: the provider emits, each square listens. */
export function createPulseBus() {
  const listeners = new Map<string, Set<PulseListener>>();
  return {
    subscribe(key: string, listener: PulseListener): () => void {
      let set = listeners.get(key);
      if (!set) listeners.set(key, (set = new Set()));
      set.add(listener);
      return () => {
        set.delete(listener);
        if (!set.size) listeners.delete(key);
      };
    },
    emit(keys: Iterable<string>): void {
      for (const key of keys)
        for (const listener of listeners.get(key) ?? []) listener();
    },
  };
}

export type PulseBus = ReturnType<typeof createPulseBus>;
