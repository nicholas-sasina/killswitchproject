import { useCallback } from "react";
import type { BurnApi } from "../api";
import { usePolling } from "../polling";
import { ErrorMessage, Status, Timestamp } from "./common";

export function BlastTimeline({
  api,
  agentId,
}: {
  api: BurnApi;
  agentId?: string;
}) {
  const load = useCallback(
    (signal: AbortSignal) => api.blastRadius(agentId!, signal),
    [api, agentId],
  );
  const { data, error } = usePolling(agentId ? load : null, 10_000);
  const blast = data && data.agent_id === agentId ? data : undefined;
  const events = [...(blast?.events ?? [])].sort((a, b) =>
    a.ts.localeCompare(b.ts),
  );
  return (
    <section className="panel" aria-labelledby="blast-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Blast radius · CloudTrail</p>
          <h2 id="blast-title">{agentId || "AWS activity timeline"}</h2>
        </div>
        <span className="muted">Refreshes every 10s</span>
      </div>
      <ErrorMessage message={error} />
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
          {!events.length ? (
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
            <ol className="timeline">
              {events.map((event, index) => (
                <li key={`${event.key_ref}-${event.ts}-${index}`}>
                  <Timestamp value={event.ts} />
                  <div>
                    <h3>{event.event_name}</h3>
                    <code>{event.key_ref}</code>
                    <p>{event.event_source || "Source unavailable"}</p>
                    {event.error_code && (
                      <p className="error">{event.error_code}</p>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          )}
          {events.length > 0 && blast?.note && (
            <p className="panel-footnote">{blast.note}</p>
          )}
        </>
      )}
    </section>
  );
}
