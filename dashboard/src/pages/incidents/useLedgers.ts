import { useCallback, useEffect, useRef, useState } from "react";
import type { BurnApi } from "../../api";
import type { LedgerEntry } from "../../contracts";

/** At most this many ledger requests at once, as on the role page. */
const AT_ONCE = 4;

export interface LedgerState {
  entries?: LedgerEntry[];
  error?: string;
}

/**
 * The ledgers of the open rows, each asked for once, the first time its row
 * opens, and kept for the life of the page. "Expand all" opens every row,
 * so requests run a few at a time, in the order the rows are listed. A
 * failed one is asked again only on `retry`.
 */
export function useLedgers(
  api: BurnApi,
  wanted: string[],
): {
  ledgers: ReadonlyMap<string, LedgerState>;
  retry: (agentId: string) => void;
} {
  const [ledgers, setLedgers] = useState<ReadonlyMap<string, LedgerState>>(
    () => new Map(),
  );
  const asked = useRef(new Set<string>());
  const inflight = useRef(new Set<string>());
  const latest = useRef(wanted);
  latest.current = wanted;
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    const own = new AbortController();
    controller.current = own;
    const pending = inflight.current;
    return () => {
      own.abort();
      // Whatever was in flight was never answered: ask again next time.
      for (const agentId of pending) asked.current.delete(agentId);
      pending.clear();
    };
  }, [api]);

  const pump = useCallback(() => {
    const signal = controller.current?.signal;
    if (!signal || signal.aborted) return;
    for (const agentId of latest.current) {
      if (inflight.current.size >= AT_ONCE) return;
      if (asked.current.has(agentId)) continue;
      asked.current.add(agentId);
      inflight.current.add(agentId);
      api
        .ledger(agentId, signal)
        .then((ledger) => {
          if (signal.aborted) return;
          setLedgers((current) =>
            new Map(current).set(agentId, { entries: ledger.entries }),
          );
        })
        .catch((failure: unknown) => {
          if (signal.aborted) return;
          const error =
            failure instanceof Error ? failure.message : String(failure);
          setLedgers((current) => new Map(current).set(agentId, { error }));
        })
        .finally(() => {
          inflight.current.delete(agentId);
          if (!signal.aborted) pump();
        });
    }
  }, [api]);

  const signature = wanted.join("\n");
  useEffect(() => {
    pump();
  }, [pump, signature]);

  const retry = useCallback(
    (agentId: string) => {
      if (inflight.current.has(agentId)) return;
      asked.current.delete(agentId);
      setLedgers((current) => {
        const next = new Map(current);
        next.delete(agentId);
        return next;
      });
      pump();
    },
    [pump],
  );

  return { ledgers, retry };
}
