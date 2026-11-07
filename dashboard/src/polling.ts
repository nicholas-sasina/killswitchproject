import { useEffect, useReducer, useState } from "react";
import type { BurnApi } from "./api";
import { eventsReducer, initialEventsState, nextCursor } from "./events";

export function useEventFeed(api: BurnApi, enabled: boolean) {
  const [state, dispatch] = useReducer(eventsReducer, initialEventsState);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState<Date>();
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let cursor: string | undefined;
    let busy = false;
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
  }, [api, enabled]);
  return { state, error, updatedAt };
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
