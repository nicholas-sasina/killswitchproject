import { Card, CardNote, DigitRoll, Figures, Notice } from "../../ui";
import { dayTime } from "../../model";
import type { DayStrip } from "./overview";

/**
 * "Last 24 hours": what the event stream shows for the day. When today's
 * history was too long to load in full, each figure is a lower bound and
 * says so.
 */
export function LastDay({ strip, now }: { strip: DayStrip; now: Date }) {
  return (
    <Card title="Last 24 hours" rule={false} className="ov-day">
      {strip.loading ? (
        <Notice>Loading the last 24 hours of events.</Notice>
      ) : (
        <>
          <Figures
            layout="grid"
            columns={3}
            items={strip.figures.map((figure) => ({
              label: figure.label,
              tone: figure.tone,
              value: (
                <>
                  {strip.lowerBound && (
                    <span className="ov-least">at least </span>
                  )}
                  <DigitRoll value={figure.value} />
                </>
              ),
            }))}
          />
          {strip.lowerBound && strip.from && (
            <CardNote>
              Counted from {dayTime(strip.from, now)}, the oldest event the
              console loaded.
            </CardNote>
          )}
        </>
      )}
    </Card>
  );
}
