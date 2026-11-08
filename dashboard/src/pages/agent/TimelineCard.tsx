import { useState } from "react";
import type { ConsoleAgent } from "../../model/console";
import { clockTime, dayTime, isSameDay, isoTime } from "../../model/time";
import { Card, CardNote, EmptyState } from "../../ui";
import type { TimelineItem } from "./story";

/** Lines drawn before "Show all". */
export const TIMELINE_CAP = 50;

const TONE_WORDS: Record<TimelineItem["tone"], string> = {
  routine: "",
  evidence: "Evidence: ",
  burn: "Burn: ",
  after: "",
};

/** "TIMELINE / EVERYTHING WORKER-07 DID", newest first. */
export function TimelineCard({
  agent,
  items,
  now,
  note,
}: {
  agent: ConsoleAgent;
  items: TimelineItem[];
  now: Date;
  /** Why the earliest events may be missing, if they are. */
  note?: string;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, TIMELINE_CAP);
  return (
    <Card
      className="agent-timeline"
      title="Timeline"
      sub={`Everything ${agent.name} did`}
    >
      {items.length ? (
        <ol className="timeline">
          {shown.map((item) => (
            <li key={item.id} className={`timeline-item tone-${item.tone}`}>
              <time className="id num" dateTime={isoTime(item.at)}>
                {isSameDay(item.at, now)
                  ? clockTime(item.at)
                  : dayTime(item.at, now)}
              </time>
              <span className="timeline-mark" aria-hidden="true" />
              <span className="timeline-text">
                {TONE_WORDS[item.tone] && (
                  <span className="visually-hidden">
                    {TONE_WORDS[item.tone]}
                  </span>
                )}
                {item.text}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <EmptyState>
          Nothing yet. What {agent.name} does appears here as it happens.
        </EmptyState>
      )}
      {items.length > TIMELINE_CAP && (
        <div className="timeline-more">
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => setAll((current) => !current)}
          >
            {all
              ? `Show the newest ${TIMELINE_CAP}`
              : `Show all ${items.length}`}
          </button>
        </div>
      )}
      {note && <CardNote>{note}</CardNote>}
    </Card>
  );
}
