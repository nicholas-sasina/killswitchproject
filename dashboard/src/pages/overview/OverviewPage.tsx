import { useMemo } from "react";
import { useConsole } from "../../data/ConsoleData";
import { burnedToday } from "../../model";
import { Columns, Hero } from "../../ui";
import { BurnedToday } from "./BurnedToday";
import { LastDay } from "./LastDay";
import { LiveFeed } from "./LiveFeed";
import { NeedsDecision, useIgnoredCallers } from "./NeedsDecision";
import { RunningNow } from "./RunningNow";
import {
  burnedRows,
  dayStrip,
  decisions,
  heroCopy,
  runningNowRows,
} from "./overview";
import "./overview.css";

/**
 * The Overview: what is running now and what was burned today. A night hero
 * with the state in one line and the two counts, a row per role with a
 * square per agent, the day's evidence, and on the right what needs a
 * person, today's burns and the live stream.
 *
 * Everything is derived once per data change from the console's shared
 * model; nothing here polls or re-renders on a timer.
 */
export function OverviewPage() {
  const {
    model,
    events,
    history,
    incidents,
    flags,
    region,
    updatedAt,
    eventsError,
    instancesLoaded,
    instancesError,
  } = useConsole();
  // Both the event stream and the instance list name agents; until both
  // have answered (or failed), a count would be a guess.
  const loading =
    !(updatedAt || eventsError) || !(instancesLoaded || instancesError);

  const burned = useMemo(() => burnedToday(model), [model]);
  const hero = useMemo(
    () =>
      heroCopy({ model, events, burned, incidents, flags, history, region }),
    [model, events, burned, incidents, flags, history, region],
  );
  const rows = useMemo(() => runningNowRows(model), [model]);
  const strip = useMemo(
    () => dayStrip(events, history, model.now),
    [events, history, model.now],
  );
  const { ignored, ignore } = useIgnoredCallers();
  const items = useMemo(
    () => decisions({ flags, events, model, ignored }),
    [flags, events, model, ignored],
  );
  const burnedList = useMemo(
    () => burnedRows(burned, incidents, events),
    [burned, incidents, events],
  );

  return (
    <div className="overview">
      {loading ? (
        <Hero eyebrow="Live" dot="quiet" title="Loading the fleet." />
      ) : (
        <Hero
          eyebrow={hero.eyebrow}
          dot={hero.incident ? "ember" : "quiet"}
          title={hero.title}
          lede={hero.lede}
          actions={
            hero.open && (
              <>
                <a className="btn btn-white" href={hero.open.href}>
                  {hero.open.label}
                </a>
                {hero.review && (
                  <a className="btn btn-ghost" href={hero.review.href}>
                    {hero.review.label}
                  </a>
                )}
              </>
            )
          }
          stats={[
            { label: "Running", value: model.counts.running },
            {
              label: "Burned today",
              value: model.counts.burnedToday,
              tone: "ember",
            },
          ]}
        />
      )}
      <Columns
        asideWidth={420}
        aside={
          <>
            <NeedsDecision items={items} onIgnore={ignore} />
            <BurnedToday rows={burnedList} />
            <LiveFeed events={events} />
          </>
        }
      >
        <RunningNow rows={rows} loading={loading} />
        <LastDay strip={strip} now={model.now} />
      </Columns>
    </div>
  );
}
