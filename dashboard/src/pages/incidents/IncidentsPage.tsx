/**
 * Incidents: every agent Burn has stopped, newest first, with why it was
 * burned, what Burn did and whether anything is still open; beside it, the
 * shared keys still waiting on their owners and how the burns started.
 *
 * The list can be searched (the query lives in the hash, as ?q=) and each
 * row opens in place to show what Burn did, what the agent saw and what is
 * still open, without leaving the page.
 *
 * Counts come from the console's one model (useConsole().incidents and
 * incidentCounts), so the navigation's Incidents number and the Open filter
 * always agree; under a search, the tabs count the rows the search kept.
 */
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useConsole } from "../../data/ConsoleData";
import { FlagActions } from "../../features/flags/FlagActions";
import { flagKey, flagTag, seenByKey } from "../../features/flags/describe";
import { routeHref, useHashParam } from "../../router";
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
import { IncidentDetail } from "./IncidentDetail";
import {
  inTab,
  matches,
  searchTerms,
  searchText,
  tabCounts,
  type IncidentFilter,
} from "./search";
import { useLedgers } from "./useLedgers";
import "./incidents.css";

type Filter = IncidentFilter;

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
    api,
  } = useConsole();
  const [show, setShow] = useHashParam("show");
  const filter: Filter = show === "open" || show === "burned" ? show : "all";
  const [query, setQuery] = useHashParam("q");
  const [everyRow, setEveryRow] = useState(false);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const search = useRef<HTMLInputElement>(null);
  useSlashToFocus(search);

  const burns = useMemo(() => burnEvents(events), [events]);
  const rows = useMemo(
    () =>
      incidents.map((incident) => {
        const cause = burnCause(incident.agent, burns);
        const why = causeSentence(cause);
        const status = statusLabel(incident);
        return {
          incident,
          cause,
          why,
          did: outcomeSentence(incident, burns, model),
          status,
          text: searchText({ incident, why, status: status.text }),
        };
      }),
    [incidents, burns, model],
  );
  const terms = searchTerms(query);
  const found = terms.length
    ? rows.filter((row) => matches(row.text, terms))
    : rows;
  const tabs = tabCounts(found);
  const matching = found.filter((row) => inTab(row.incident, filter));
  const shown = everyRow ? matching : matching.slice(0, ROW_CAP);
  const causes = causeCounts(rows.map((row) => row.cause));

  const openIds = shown
    .filter((row) => expanded.has(row.incident.agent.key))
    .map((row) => row.incident.agent.agentId);
  const { ledgers, retry } = useLedgers(api, openIds);
  const toggle = (key: string) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const allOpen =
    shown.length > 0 &&
    shown.every((row) => expanded.has(row.incident.agent.key));
  const clearSearch = () => {
    setQuery(undefined);
    search.current?.focus();
  };

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
    { value: "all", label: "All", count: tabs.all },
    { value: "open", label: "Open", count: tabs.open },
    { value: "burned", label: "Burned", count: tabs.burned },
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
          sub={
            !instancesLoaded
              ? undefined
              : terms.length
                ? `${found.length} of ${counts.all} match`
                : `${counts.all}, newest first`
          }
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
          ) : (
            <>
              <div className="inc-tools">
                <label className="inc-search">
                  <span className="visually-hidden">Search incidents</span>
                  <input
                    ref={search}
                    type="search"
                    value={query ?? ""}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Search by agent, role, key, reason or status"
                    aria-keyshortcuts="/"
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <kbd className="inc-search-key" aria-hidden="true">
                    /
                  </kbd>
                </label>
                <div className="inc-tools-buttons">
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={!shown.length || allOpen}
                    onClick={() =>
                      setExpanded(
                        (current) =>
                          new Set([
                            ...current,
                            ...shown.map((row) => row.incident.agent.key),
                          ]),
                      )
                    }
                  >
                    Expand all
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={!expanded.size}
                    onClick={() => setExpanded(new Set())}
                  >
                    Collapse all
                  </button>
                </div>
              </div>
              {!matching.length ? (
                <EmptyState
                  action={
                    terms.length ? (
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={clearSearch}
                      >
                        Clear
                      </button>
                    ) : undefined
                  }
                >
                  {terms.length
                    ? found.length
                      ? `No ${filter} incidents match “${query?.trim()}”.`
                      : `No incidents match “${query?.trim()}”.`
                    : filter === "open"
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
                    <span />
                  </div>
                  <ol className="inc-rows">
                    {shown.map(({ incident, why, did, status }) => {
                      const { agent } = incident;
                      const when = burnWhen(agent, model.now);
                      const isOpen = expanded.has(agent.key);
                      const detailId = `inc-detail-${agent.key}`;
                      return (
                        <li
                          key={agent.key}
                          className={isOpen ? "inc-item is-open" : "inc-item"}
                        >
                          <button
                            type="button"
                            className="inc-row"
                            aria-expanded={isOpen}
                            aria-controls={detailId}
                            aria-label={`${agent.name}, ${when}. Why it was burned: ${why}. What Burn did: ${did}. Status: ${status.text}.`}
                            onClick={() => toggle(agent.key)}
                          >
                            <span className="inc-agent">
                              <span
                                className="square square-burned square-14"
                                aria-hidden="true"
                              />
                              <span>
                                <span className="inc-name id">
                                  {agent.name}
                                </span>
                                <span className="inc-when">{when}</span>
                              </span>
                            </span>
                            <span className="inc-text inc-why">
                              <span
                                className="inc-label cap"
                                aria-hidden="true"
                              >
                                Why
                              </span>
                              {why}
                            </span>
                            <span className="inc-text inc-did">
                              <span
                                className="inc-label cap"
                                aria-hidden="true"
                              >
                                What Burn did
                              </span>
                              {did}
                            </span>
                            <span className="inc-status">
                              <Pill tone={status.tone}>{status.text}</Pill>
                            </span>
                            <span className="inc-chevron" aria-hidden="true" />
                          </button>
                          <div
                            id={detailId}
                            className="inc-detail"
                            hidden={!isOpen}
                          >
                            {isOpen && (
                              <IncidentDetail
                                incident={incident}
                                events={events}
                                model={model}
                                flags={flags}
                                ledger={ledgers.get(agent.agentId)}
                                onRetry={() => retry(agent.agentId)}
                              />
                            )}
                          </div>
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
                </>
              )}
              <CardNote>
                Open a row for what Burn did, what the agent saw and what is
                still open. Its own page has the full ledger, timeline, blast
                radius and replacement.
              </CardNote>
            </>
          )}
        </Card>
      </Columns>
    </div>
  );
}

/** "/" puts the cursor in the search box, unless the cursor is in a field already. */
function useSlashToFocus(target: RefObject<HTMLInputElement | null>) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.defaultPrevented) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const from = event.target instanceof Element ? event.target : null;
      if (from?.closest("input, textarea, select, [contenteditable]")) return;
      const input = target.current;
      if (!input || input.closest("[inert]")) return;
      event.preventDefault();
      input.focus();
      input.select();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [target]);
}
