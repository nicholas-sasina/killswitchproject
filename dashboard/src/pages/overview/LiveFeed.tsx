import { memo, useEffect, useRef } from "react";
import type { LedgerEvent } from "../../contracts";
import { useConsole } from "../../data/ConsoleData";
import {
  clock,
  eventSentence,
  isoTime,
  type SentenceContext,
} from "../../model";
import { Card, Crossfade, Notice } from "../../ui";
import { LIVE_ROWS } from "./overview";

const LiveRow = memo(function LiveRow({
  event,
  context,
  appear,
}: {
  event: LedgerEvent;
  context: SentenceContext;
  appear: boolean;
}) {
  const said = eventSentence(event, context);
  return (
    <li className="ov-live-row">
      <time className="ov-live-at id num" dateTime={isoTime(event.ts)}>
        {clock(event.ts)}
      </time>
      <Crossfade id={event.event_id} appear={appear} className="ov-live-text">
        <span>
          <b className={said.evidence ? "is-ember" : undefined}>{said.who}</b>
          {said.what.startsWith("'s ") ? said.what : ` ${said.what}`}
        </span>
      </Crossfade>
    </li>
  );
});

/**
 * "Live": the newest events as plain sentences. Rows already on screen stay
 * still; a row that arrives with a poll crossfades in at the top.
 */
export function LiveFeed({ events }: { events: LedgerEvent[] }) {
  const { sentenceContext, updatedAt } = useConsole();
  // Rows mounted on the first paint never animate; later ones do.
  const painted = useRef(false);
  useEffect(() => {
    painted.current = true;
  }, []);
  const rows = events.slice(0, LIVE_ROWS);
  return (
    <Card title="Live" sub="Newest first" pad="narrow" className="ov-live">
      {rows.length ? (
        <ol>
          {rows.map((event) => (
            <LiveRow
              key={event.event_id}
              event={event}
              context={sentenceContext}
              appear={painted.current}
            />
          ))}
        </ol>
      ) : updatedAt ? (
        <p className="ov-empty">
          Nothing yet. Events appear here as agents use tools and keys.
        </p>
      ) : (
        <div className="ov-card-notice">
          <Notice>Loading events.</Notice>
        </div>
      )}
    </Card>
  );
}
