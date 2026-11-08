import type { EventAction, LedgerEvent } from "../contracts";
import { Timestamp } from "./common";

const actions: Record<EventAction, { text: string }> = {
  recorded: { text: "Credential recorded" },
  inherited: { text: "Exposure inherited" },
  blocked: { text: "Outbound leak blocked" },
  refused: { text: "Agent call refused" },
  revoked: { text: "Agent badge revoked" },
  rotated: { text: "Credential rotated" },
  flagged: { text: "Credential flagged for review" },
  rejected: { text: "Provider request rejected" },
  issued: { text: "Session or credential issued" },
  started: { text: "Agent instance started" },
  exited: { text: "Agent instance exited" },
  unregistered: { text: "Unregistered agent refused" },
  canary_used: { text: "Canary credential used" },
  dismissed: { text: "Flag dismissed by owner" },
  replaced: { text: "Burned instance replaced" },
  resumed: { text: "Replacement resumed the task" },
};

/**
 * The wording for an action, tolerating one this build has not heard of.
 *
 * `action` is typed as a closed union, but it arrives over HTTP: if an action
 * is added to contracts/ledger-event.schema.json and the API ships before the
 * dashboard mirrors it, an unguarded lookup here would return undefined and
 * crash the whole stream. A new action instead shows as readable prose until
 * someone gives it proper wording.
 */
function describe(action: string): string {
  const known = actions[action as EventAction];
  if (known) return known.text;
  const words = action.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function EventFeed({ events }: { events: LedgerEvent[] }) {
  return (
    <section className="panel feed-panel" aria-labelledby="feed-title">
      <div className="panel-heading">
        <h2 id="feed-title">Live events</h2>
        <span className="muted">Newest first · {events.length}</span>
      </div>
      {!events.length ? (
        <p className="empty">
          Nothing yet. Events appear here as agents use tools and credentials.
        </p>
      ) : (
        <ol className="event-list">
          {events.slice(0, 200).map((event, index) => (
            <li key={event.event_id} className={`event event-${event.action}`}>
              {/* A line number, like a printout. Severity is carried by the
                    ember spine and the title, so a glyph here would be a
                    third encoding of the same fact. */}
              <span className="event-icon" aria-hidden="true">
                {String(index + 1).padStart(3, "0")}
              </span>
              <div className="event-body">
                <div className="event-title">
                  <strong>{describe(event.action)}</strong>
                  <Timestamp value={event.ts} />
                </div>
                <p>
                  <b>{event.agent_id}</b>
                  <span className="muted">
                    {" "}
                    · {event.source.replaceAll("_", " ")}
                    {event.actor && ` · ${event.actor}`}
                  </span>
                </p>
                {event.secret_ref && <code>{event.secret_ref}</code>}
                {(event.tool ||
                  event.destination ||
                  event.encoding ||
                  event.target_agent_id) && (
                  <p className="event-meta">
                    {[
                      event.tool,
                      event.destination,
                      event.encoding && `encoding: ${event.encoding}`,
                      event.target_agent_id && `to ${event.target_agent_id}`,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                )}
                {event.detail && <p className="event-detail">{event.detail}</p>}
              </div>
            </li>
          ))}
        </ol>
      )}
      {events.length > 200 && (
        <p className="panel-footnote">
          Showing the latest 200 of {events.length} events received.
        </p>
      )}
    </section>
  );
}
