import { useCallback, useEffect, useRef, useState } from "react";
import type { BurnApi } from "../../api";
import { keysSeen, type KeysSeen } from "./model";

/** At most this many ledger requests at once. */
const AT_ONCE = 4;

export interface LedgerWanted {
  agentId: string;
  /** Its key count from /instances: a change means its ledger changed. */
  exposures?: number;
}

/**
 * How each shown agent saw its keys (plain text or references), from its
 * ledger. The API has one ledger route per agent and no batch, so this
 * asks only for the rows on screen that hold keys, a few at a time, and
 * asks again for an agent only when its key count changes. It never polls
 * on a timer, and it stops when the page unmounts.
 */
export function useKeysSeen(
  api: BurnApi,
  wanted: LedgerWanted[],
): ReadonlyMap<string, KeysSeen> {
  const [seen, setSeen] = useState<ReadonlyMap<string, KeysSeen>>(
    () => new Map(),
  );
  // The key count each agent's ledger was last asked for.
  const asked = useRef(new Map<string, number>());
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
      pending.clear();
      // Whatever was in flight was never answered: ask again next time.
      asked.current = new Map();
    };
  }, [api]);

  const pump = useCallback(() => {
    const signal = controller.current?.signal;
    if (!signal || signal.aborted) return;
    for (const item of latest.current) {
      if (inflight.current.size >= AT_ONCE) return;
      const count = item.exposures ?? 0;
      if (
        count <= 0 ||
        inflight.current.has(item.agentId) ||
        asked.current.get(item.agentId) === count
      )
        continue;
      inflight.current.add(item.agentId);
      asked.current.set(item.agentId, count);
      api
        .ledger(item.agentId, signal)
        .then((ledger) => {
          if (signal.aborted) return;
          setSeen((current) =>
            new Map(current).set(item.agentId, keysSeen(ledger.entries)),
          );
        })
        .catch(() => {
          // Left unknown: the row keeps the key count from /instances.
        })
        .finally(() => {
          inflight.current.delete(item.agentId);
          if (!signal.aborted) pump();
        });
    }
  }, [api]);

  const signature = wanted
    .filter((item) => (item.exposures ?? 0) > 0)
    .map((item) => `${item.agentId}:${item.exposures}`)
    .join("|");
  useEffect(() => {
    pump();
  }, [pump, signature]);

  return seen;
}
