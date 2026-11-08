import { useEffect, useReducer, useState } from "react";
import type { BurnApi } from "./api";
import {
  eventIdAt,
  eventsReducer,
  initialEventsState,
  nextCursor,
} from "./events";

/** Events per page while loading history, the most the API returns at once. */
export const HISTORY_PAGE = 500;
/** Pages of history to load at most, so a very busy day stays bounded. */
export const HISTORY_MAX_PAGES = 40;

/**
 * How far back the loaded events reach. `complete` means every event since
 * `from` is loaded; otherwise `from` is the oldest event loaded, and counts
 * over the stream are lower bounds.
 */
export interface EventHistory {
  from?: string;
  complete: boolean;
}

/**
 * The live event stream, polled once a second.
 *
 * The first poll returns the newest 200 events, which at a busy swarm is
 * minutes, not a day. With `historySince` (a time in ms) the feed then pages
 * forward from that moment, 500 events at a time, until it meets the first
 * poll's events, so "today" and "the last 24 hours" are counted from real
 * history. The live poll keeps running meanwhile.
 */
export function useEventFeed(
  api: BurnApi,
  enabled: boolean,
  options: { historySince?: number } = {},
) {
  const [state, dispatch] = useReducer(eventsReducer, initialEventsState);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState<Date>();
  const [history, setHistory] = useState<EventHistory>({ complete: false });
  const { historySince } = options;
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let cursor: string | undefined;
    let busy = false;
    let first = true;
    async function backfill(since: number, until: string | undefined) {
      let after = eventIdAt(since);
      let oldest: string | undefined;
      for (let page = 0; page < HISTORY_MAX_PAGES; page += 1) {
        const response = await api.events(
          after,
          controller.signal,
          HISTORY_PAGE,
        );
        if (controller.signal.aborted) return;
        dispatch(response);
        oldest ??= response.events[0]?.ts;
        const last = response.events.at(-1)?.event_id;
        if (
          !last ||
          response.events.length < HISTORY_PAGE ||
          (until && last >= until)
        ) {
          setHistory({ from: new Date(since).toISOString(), complete: true });
          return;
        }
        after = last;
      }
      setHistory({ from: oldest, complete: false });
    }
    async function poll() {
      if (busy) return;
      busy = true;
      try {
        const response = await api.events(cursor, controller.signal);
        if (!controller.signal.aborted) {
          cursor = nextCursor(cursor, response);
          dispatch(response);
          setUpdatedAt(new Date());
          setError("");
          if (first) {
            first = false;
            // Fewer than a full first page means the stream holds
            // everything there is.
            if (historySince === undefined || response.events.length < 200)
              setHistory({
                from:
                  historySince === undefined
                    ? response.events[0]?.ts
                    : new Date(historySince).toISOString(),
                complete: historySince !== undefined,
              });
            else
              void backfill(historySince, response.events[0]?.event_id).catch(
                () => {
                  if (!controller.signal.aborted)
                    setHistory({
                      from: response.events[0]?.ts,
                      complete: false,
                    });
                },
              );
          }
        }
      } catch (error) {
        if (!controller.signal.aborted)
          setError(
            error instanceof Error ? error.message : "Could not load events.",
          );
      } finally {
        busy = false;
      }
    }
    void poll();
    const timer = window.setInterval(() => void poll(), 1_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [api, enabled, historySince]);
  return { state, error, updatedAt, history };
}

export function usePolling<T>(
  load: ((signal: AbortSignal) => Promise<T>) | null,
  interval: number,
) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  useEffect(() => {
    setData(undefined);
    setError("");
    if (!load) return;
    const controller = new AbortController();
    let busy = false;
    async function poll() {
      if (busy || !load) return;
      busy = true;
      try {
        const result = await load(controller.signal);
        if (!controller.signal.aborted) {
          setData(result);
          setError("");
        }
      } catch (error) {
        if (!controller.signal.aborted)
          setError(error instanceof Error ? error.message : "Request failed.");
      } finally {
        busy = false;
      }
    }
    void poll();
    const timer = window.setInterval(() => void poll(), interval);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [load, interval]);
  return { data, error };
}
