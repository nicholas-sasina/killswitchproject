import { useCallback, useEffect, useRef, useState } from "react";

/**
 * One shared collection, polled on an interval with cancellation and no
 * overlapping requests (the same rules as polling.ts). Unlike usePolling it
 * keeps its data across `refresh()`, so a page can ask for an answer now
 * (after a burn, after resolving a flag) without flashing empty.
 */
export function useSharedPoll<T>(
  load: ((signal: AbortSignal) => Promise<T>) | null,
  interval: number,
) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [nudge, setNudge] = useState(0);
  const latest = useRef(load);
  latest.current = load;
  const enabled = Boolean(load);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let busy = false;
    async function poll() {
      const run = latest.current;
      if (busy || !run) return;
      busy = true;
      try {
        const result = await run(controller.signal);
        if (!controller.signal.aborted) {
          // An answer equal to the last keeps the same object, so the
          // pages that read it do not re-render every five seconds.
          setData((current) =>
            current !== undefined &&
            JSON.stringify(current) === JSON.stringify(result)
              ? current
              : result,
          );
          setError("");
        }
      } catch (caught) {
        if (!controller.signal.aborted)
          setError(
            caught instanceof Error ? caught.message : "Request failed.",
          );
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
  }, [enabled, interval, nudge, load]);
  const refresh = useCallback(() => setNudge((n) => n + 1), []);
  return { data, error, refresh };
}
