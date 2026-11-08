/**
 * CloudTrail history for a burned agent's AWS keys.
 *
 * Collapsible and cheap: closed means no requests at all, the grouping is
 * memoised, calls are grouped by key so repetition collapses into one line
 * with a count, and each group renders a bounded number of rows behind a
 * "show all" control. One row per call is unreadable once a key has been
 * used a few dozen times.
 */
import { useCallback, useMemo, useState } from "react";
import type { BurnApi } from "../api";
import type { BlastEvent } from "../contracts";
import { usePolling } from "../polling";
import { Collapsible } from "./Collapsible";
import { ErrorMessage, Status, Timestamp } from "./common";

/** Rows drawn per key before the group is truncated. */
const ROW_CAP = 8;

function TrailGroup({
  keyRef,
  events,
}: {
  keyRef: string;
  events: BlastEvent[];
}) {
  const [all, setAll] = useState(false);
  const shown = all ? events : events.slice(0, ROW_CAP);
  const failures = events.filter((event) => event.error_code).length;
  return (
    <div className="trail-group">
      <div className="trail-group-head">
        <code>{keyRef || "AWS key reference unavailable"}</code>
        <span className="count">
          {events.length} {events.length === 1 ? "call" : "calls"}
          {failures ? ` · ${failures} refused` : ""}
        </span>
      </div>
      <ol className="trail-rows">
        {shown.map((event, index) => (
          <li
            className="trail-row"
            key={`${event.ts}-${event.event_name}-${index}`}
          >
            <Timestamp value={event.ts} />
            <span className="call">
              {event.event_name}
              {event.error_code && (
                <span className="fail"> · {event.error_code}</span>
              )}
            </span>
            <span className="src">
              {event.event_source || "Source unavailable"}
            </span>
          </li>
        ))}
      </ol>
      {events.length > ROW_CAP && (
        <div className="trail-more">
          <button
            type="button"
            className="secondary-button"
            onClick={() => setAll((current) => !current)}
          >
            {all ? `Show first ${ROW_CAP}` : `Show all ${events.length} calls`}
          </button>
        </div>
      )}
    </div>
  );
}

export function BlastTimeline({
  api,
  agentId,
}: {
  api: BurnApi;
  agentId?: string;
}) {
  // Mirrors the module's open state so a closed timeline issues no requests.
  const [open, setOpen] = useState(false);
  const load = useCallback(
    (signal: AbortSignal) => api.blastRadius(agentId!, signal),
    [api, agentId],
  );
  const { data, error } = usePolling(agentId && open ? load : null, 10_000);
  const blast = data && data.agent_id === agentId ? data : undefined;

  // Group by key, newest first inside each group. Memoised so scrolling and
  // unrelated re-renders do not re-sort the history.
  const groups = useMemo(() => {
    const byKey = new Map<string, BlastEvent[]>();
    for (const event of blast?.events ?? []) {
      const list = byKey.get(event.key_ref);
      if (list) list.push(event);
      else byKey.set(event.key_ref, [event]);
    }
    for (const list of byKey.values())
      list.sort((a, b) => b.ts.localeCompare(a.ts));
    return [...byKey.entries()];
  }, [blast]);

  const total = blast?.events.length ?? 0;
  const summary = !agentId
    ? "Nothing burned yet"
    : !blast
      ? open
        ? "Checking…"
        : "Closed, not checking"
      : `${total} AWS call${total === 1 ? "" : "s"} recorded`;

  return (
    <Collapsible
      label="Blast radius"
      summary={summary}
      openWhen={Boolean(agentId)}
      onOpenChange={setOpen}
    >
      <ErrorMessage message={error} />
      <p className="panel-intro">
        Every AWS call made with this agent's keys, from CloudTrail, before and
        after the burn.
      </p>
      {!agentId ? (
        <p className="empty">
          Burn an agent, or select a revoked agent, to review activity from its
          AWS keys.
        </p>
      ) : (
        <>
          {blast?.keys.length ? (
            <ul className="key-list">
              {blast.keys.map((key, index) => (
                <li key={key.key_ref ?? index}>
                  <code>{key.key_ref || "AWS key reference unavailable"}</code>
                  {key.status && <Status value={key.status} />}
                </li>
              ))}
            </ul>
          ) : null}
          {!groups.length ? (
            <div className="empty">
              <p>
                {blast
                  ? "No CloudTrail activity returned yet."
                  : "Checking CloudTrail activity…"}
              </p>
              <p className="lag-note">
                {blast?.note ||
                  "CloudTrail event history can lag 5–15 minutes behind real activity. This timeline refreshes automatically."}
              </p>
            </div>
          ) : (
            <div className="trail-scroll">
              {groups.map(([keyRef, events]) => (
                <TrailGroup key={keyRef} keyRef={keyRef} events={events} />
              ))}
            </div>
          )}
          {groups.length > 0 && blast?.note && (
            <p className="panel-footnote">{blast.note}</p>
          )}
          <p className="panel-footnote">
            Updates every ten seconds while open. Collapsing stops the check.
          </p>
        </>
      )}
    </Collapsible>
  );
}
