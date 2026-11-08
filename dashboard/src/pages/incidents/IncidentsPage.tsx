/**
 * Incidents: every agent Burn has stopped, newest first, with why it was
 * burned, what Burn did and whether anything is still open; beside it, the
 * shared keys still waiting on their owners and how the burns started.
 *
 * Counts come from the console's one model (useConsole().incidents and
 * incidentCounts), so the navigation's Incidents number and the Open filter
 * always agree.
 */
import { useMemo, useState } from "react";
import { useConsole } from "../../data/ConsoleData";
import { FlagActions } from "../../features/flags/FlagActions";
import { flagKey, flagTag, seenByKey } from "../../features/flags/describe";
import { agentHref, routeHref, useHashParam } from "../../router";
import {
  Card,
  CardNote,
  Columns,
  EmptyState,
  Hero,
  Notice,
  Pill,
  type SegmentOption,
  Segmented,
} from "../../ui";
import {
  burnCause,
  burnedTodayLine,
  burnEvents,
  burnWhen,
  causeCounts,
  causeSentence,
  keysWaitingLine,
  outcomeSentence,
  statusLabel,
  stillOpenBody,
} from "./describe";
import "./incidents.css";

type Filter = "all" | "open" | "burned";

/** Rows drawn before "Show all". */
const ROW_CAP = 60;
/** Keys in "Still open" before the link to Credentials. */
const OPEN_CAP = 4;

export function IncidentsPage() {
  const {
    model,
    events,
    incidents,
    incidentCounts: counts,
    flags,
    flagsLoaded,
    flagsError,
    instancesLoaded,
  } = useConsole();
  const [show, setShow] = useHashParam("show");
  const filter: Filter = show === "open" || show === "burned" ? show : "all";
  const [everyRow, setEveryRow] = useState(false);

  const burns = useMemo(() => burnEvents(events), [events]);
  const rows = useMemo(
    () =>
      incidents.map((incident) => {
        const cause = burnCause(incident.agent, burns);
        return {
          incident,
          cause,
          why: causeSentence(cause),
          did: outcomeSentence(incident, burns, model),
          status: statusLabel(incident),
        };
      }),
    [incidents, burns, model],
  );
  const matching = rows.filter(
    (row) =>
      filter === "all" ||
      (filter === "open" ? row.incident.open : !row.incident.open),
  );
  const shown = everyRow ? matching : matching.slice(0, ROW_CAP);
  const causes = causeCounts(rows.map((row) => row.cause));

  const open = useMemo(
    () => flags.filter((flag) => flag.state === "open"),
    [flags],
  );
  const listed = open.slice(0, OPEN_CAP);
  const listedRefs = useMemo(
    () => new Set(listed.map((flag) => flag.secret_ref)),
    // The refs, not the array: a new poll with the same keys changes nothing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [listed.map((flag) => flag.secret_ref).join("\n")],
  );
  const seen = useMemo(
    () => seenByKey(events, model, listedRefs),
    [events, model, listedRefs],
  );

  const title = [
    instancesLoaded ? burnedTodayLine(model.counts.burnedToday) : "",
    flagsLoaded ? keysWaitingLine(counts.keysWaiting) : "",
  ];
  const options: SegmentOption<Filter>[] = [
    { value: "all", label: "All", count: counts.all },
    { value: "open", label: "Open", count: counts.open },
    { value: "burned", label: "Burned", count: counts.closed },
  ];

  return (
    <div className="incidents-page">
      <Hero
        eyebrow="Incidents / burned agents"
        dot={counts.open || model.counts.burnedToday ? "ember" : "quiet"}
        title={title.some(Boolean) ? title : "Incidents"}
        lede="Every agent Burn has stopped, newest first. Running agents live on the overview; once an agent is burned it moves here, with what it saw, what Burn did and what is still open."
      />
      <Columns
        aside={
          <>
            <Card
              title="Still open"
              sub={flagsLoaded ? counts.keysWaiting : undefined}
              pad="narrow"
              className="inc-open"
            >
              {flagsError && (
                <Notice tone="error">
                  {flagsLoaded
                    ? `Flagged keys may be out of date: ${flagsError}`
                    : `Flagged keys could not be loaded: ${flagsError} Burn retries every 5 seconds.`}
                </Notice>
              )}
              {!flagsLoaded ? (
                !flagsError && <Notice>Loading flagged keys…</Notice>
              ) : !open.length ? (
                <EmptyState>
                  Nothing is open. When a burned agent saw a shared key, the key
                  waits here for its owner to rotate it or dismiss it.
                </EmptyState>
              ) : (
                <ul className="inc-open-list">
                  {listed.map((flag) => (
                    <li key={flag.secret_ref}>
                      <p className="cap inc-open-tag">{flagTag(flag)}</p>
                      <p className="id inc-open-key" title={flag.secret_ref}>
                        {flagKey(flag)}
                      </p>
                      <p className="inc-open-body">
                        {stillOpenBody(
                          flag,
                          incidents,
                          seen.get(flag.secret_ref),
                          model,
                        )}
                      </p>
                      <FlagActions flag={flag} />
                    </li>
                  ))}
                </ul>
              )}
              {open.length > OPEN_CAP && (
                <CardNote>
                  <a
                    className="text-link"
                    href={routeHref({ page: "credentials" })}
                  >
                    All {open.length} in Credentials
                  </a>
                </CardNote>
              )}
            </Card>
            {causes.canary + causes.person + causes.source > 0 && (
              <Card
                title="How burns started"
                sub="Last 24 hours"
                pad="narrow"
                className="inc-how"
              >
                <dl className="inc-how-list">
                  {(
                    [
                      ["Canary used from outside", causes.canary],
                      ["A person pressed Burn", causes.person],
                      ["Everyone who read a bad source", causes.source],
                    ] as const
                  )
                    .filter(([, count]) => count > 0)
                    .map(([label, count]) => (
                      <div key={label}>
                        <dt>{label}</dt>
                        <dd className="num">{count}</dd>
                      </div>
                    ))}
                </dl>
                {causes.unknown > 0 && (
                  <CardNote>
                    {causes.unknown === 1
                      ? "For 1 other burn, the loaded events don’t name who or what started it."
                      : `For ${causes.unknown} other burns, the loaded events don’t name who or what started them.`}
                  </CardNote>
                )}
              </Card>
            )}
          </>
        }
      >
        <Card
          title="Burned agents"
          sub={instancesLoaded ? `${counts.all}, newest first` : undefined}
          aside={
            instancesLoaded && counts.all > 0 ? (
              <Segmented
                label="Show"
                options={options}
                value={filter}
                onChange={(value) =>
                  setShow(value === "all" ? undefined : value)
                }
              />
            ) : undefined
          }
          className="inc-list"
        >
          {!instancesLoaded ? (
            <Notice>Loading burned agents…</Notice>
          ) : !counts.all ? (
            <EmptyState>
              No agent has been burned. When Burn stops an agent, it moves here
              with why it was burned, what Burn did and what is still open.
            </EmptyState>
          ) : !matching.length ? (
            <EmptyState>
              {filter === "open"
                ? "Nothing is open. Every burned agent's shared keys have been decided."
                : "No incident is closed yet. One closes once its shared keys are decided and its replacement is running."}
            </EmptyState>
          ) : (
            <>
              <div className="inc-head cap" aria-hidden="true">
                <span>Agent</span>
                <span>Why it was burned</span>
                <span>What Burn did</span>
                <span>Status</span>
              </div>
              <ol className="inc-rows">
                {shown.map(({ incident, why, did, status }) => {
                  const { agent } = incident;
                  const when = burnWhen(agent, model.now);
                  return (
                    <li key={agent.key}>
                      <a
                        className="inc-row"
                        href={agentHref(agent.sessionId ?? agent.key)}
                        aria-label={`${agent.name}, ${when}. Why it was burned: ${why}. What Burn did: ${did}. Status: ${status.text}.`}
                      >
                        <span className="inc-agent">
                          <span
                            className="square square-burned square-14"
                            aria-hidden="true"
                          />
                          <span>
                            <span className="inc-name id">{agent.name}</span>
                            <span className="inc-when">{when}</span>
                          </span>
                        </span>
                        <span className="inc-text inc-why">
                          <span className="inc-label cap" aria-hidden="true">
                            Why
                          </span>
                          {why}
                        </span>
                        <span className="inc-text inc-did">
                          <span className="inc-label cap" aria-hidden="true">
                            What Burn did
                          </span>
                          {did}
                        </span>
                        <span className="inc-status">
                          <Pill tone={status.tone}>{status.text}</Pill>
                        </span>
                      </a>
                    </li>
                  );
                })}
              </ol>
              {matching.length > ROW_CAP && (
                <div className="inc-more">
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => setEveryRow((current) => !current)}
                  >
                    {everyRow
                      ? `Show the first ${ROW_CAP}`
                      : `Show all ${matching.length}`}
                  </button>
                </div>
              )}
              <CardNote>
                Open an incident for the agent&rsquo;s exposure ledger,
                timeline, blast radius and replacement.
              </CardNote>
            </>
          )}
        </Card>
      </Columns>
    </div>
  );
}
