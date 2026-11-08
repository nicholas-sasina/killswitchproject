import type { EventAction, LedgerEvent } from "../contracts";
import { Timestamp } from "./common";

const actions: Record<EventAction, { icon: string; text: string }> = {
  recorded: { icon: "+", text: "Credential recorded" },
  inherited: { icon: "↳", text: "Exposure inherited" },
  blocked: { icon: "⊘", text: "Outbound leak blocked" },
  refused: { icon: "×", text: "Agent call refused" },
  revoked: { icon: "■", text: "Agent badge revoked" },
  rotated: { icon: "↻", text: "Credential rotated" },
  flagged: { icon: "⚑", text: "Credential flagged for review" },
  rejected: { icon: "!", text: "Provider request rejected" },
  issued: { icon: "✓", text: "Session or credential issued" },
  started: { icon: "▶", text: "Agent instance started" },
  exited: { icon: "◼", text: "Agent instance exited" },
  unregistered: { icon: "?", text: "Unregistered agent refused" },
  canary_used: { icon: "⚠", text: "Canary credential used" },
  dismissed: { icon: "✓", text: "Flag dismissed by owner" },
};

export function EventFeed({ events }: { events: LedgerEvent[] }) {
  return (
    <section className="panel feed-panel" aria-labelledby="feed-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Activity stream</p>
          <h2 id="feed-title">Live events</h2>
        </div>
        <span className="muted">Newest first · {events.length}</span>
      </div>
      {!events.length ? (
        <p className="empty">
          Waiting for activity. Events will appear as agents use tools and
          credentials.
        </p>
      ) : (
        <ol className="event-list">
          {events.slice(0, 200).map((event) => {
            const action = actions[event.action];
            return (
              <li
                key={event.event_id}
                className={`event event-${event.action}`}
              >
                <span className="event-icon" aria-hidden="true">
                  {action.icon}
                </span>
                <div className="event-body">
                  <div className="event-title">
                    <strong>{action.text}</strong>
                    <Timestamp value={event.ts} />
                  </div>
                  <p>
                    <b>{event.agent_id}</b>
                    <span className="muted">
                      {" "}
                      · {event.source.replaceAll("_", " ")}
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
                  {event.detail && (
                    <p className="event-detail">{event.detail}</p>
                  )}
                </div>
              </li>
            );
          })}
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
