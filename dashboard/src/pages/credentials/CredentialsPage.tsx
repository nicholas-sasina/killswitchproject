/**
 * Credentials: the keys agents saw but do not own. Burn never rotates
 * those itself; each one waits here for its owner to rotate it, or to
 * dismiss the flag with a reason. Decided keys sit under Resolved.
 */
import { useMemo } from "react";
import type { FlagLevel } from "../../contracts";
import { useConsole } from "../../data/ConsoleData";
import { FlagActions } from "../../features/flags/FlagActions";
import {
  evidenceAgent,
  evidenceSentence,
  firstEvidence,
  isShared,
  LEVELS,
  levelRank,
  seenByKey,
} from "../../features/flags/describe";
import {
  dayTime,
  findAgent,
  type ConsoleAgent,
  type ConsoleModel,
} from "../../model";
import { plural } from "../../plural";
import { agentHref, useHashParam } from "../../router";
import {
  Card,
  CardNote,
  Columns,
  EmptyState,
  Hero,
  Notice,
  Pill,
  Segmented,
  type SegmentOption,
} from "../../ui";
import { keysWaitingLine } from "../incidents/describe";
import {
  credentialCounts,
  credentialRows,
  decisionLines,
  inFilter,
  type CredentialFilter,
  type CredentialRow,
} from "./rows";
import "./credentials.css";

/** Agents named in "Seen by" before "and N more". */
const SEEN_CAP = 3;

export function CredentialsPage() {
  const { model, events, flags, flagsLoaded, flagsError, incidentCounts } =
    useConsole();
  const [show, setShow] = useHashParam("show");
  const filter: CredentialFilter =
    show === "resolved" || show === "all" ? show : "open";

  const rows = useMemo(() => credentialRows(flags, events), [flags, events]);
  const counts = credentialCounts(rows);
  const refsKey = rows.map((row) => row.ref).join("\n");
  const refs = useMemo(() => new Set(refsKey.split("\n")), [refsKey]);
  const seen = useMemo(
    () => seenByKey(events, model, refs),
    [events, model, refs],
  );
  const shown = rows.filter((row) => inFilter(row, filter));
  const options: SegmentOption<CredentialFilter>[] = [
    { value: "open", label: "Open", count: counts.open },
    { value: "resolved", label: "Resolved", count: counts.resolved },
    { value: "all", label: "All", count: counts.all },
  ];

  return (
    <div className="credentials-page">
      <Hero
        eyebrow="Credentials / flagged keys"
        dot={incidentCounts.keysWaiting ? "ember" : "quiet"}
        title={
          flagsLoaded
            ? keysWaitingLine(incidentCounts.keysWaiting)
            : "Credentials"
        }
        lede="Keys an agent saw but doesn't own. Burn never rotates these itself: each waits here for its owner to rotate it, or to dismiss the flag with a reason. A dismissed flag reopens if stronger evidence arrives."
      />
      <Columns>
        <Card
          title="Flagged keys"
          sub={flagsLoaded ? `${counts.open} open` : undefined}
          aside={
            flagsLoaded && counts.all > 0 ? (
              <Segmented
                label="Show"
                options={options}
                value={filter}
                onChange={(value) =>
                  setShow(value === "open" ? undefined : value)
                }
              />
            ) : undefined
          }
          className="cred-card"
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
          ) : !counts.all ? (
            <EmptyState>
              No flagged keys. When an agent sees a key it doesn&rsquo;t own,
              Burn flags the key here for its owner instead of rotating it.
            </EmptyState>
          ) : !shown.length ? (
            <EmptyState>
              {filter === "open"
                ? "No key waits on its owner. Decided keys are under Resolved."
                : "Nothing resolved yet. Keys their owners rotate or dismiss appear here."}
            </EmptyState>
          ) : (
            <table className="table cred-table">
              <thead>
                <tr>
                  <th scope="col">Key</th>
                  <th scope="col">Owner</th>
                  <th scope="col">Level</th>
                  <th scope="col">Seen by</th>
                  <th scope="col">First evidence</th>
                  <th scope="col" className="is-end">
                    Decision
                  </th>
                </tr>
              </thead>
              <tbody>
                {shown.map((row) => (
                  <Row
                    key={row.ref}
                    row={row}
                    seen={seen.get(row.ref) ?? []}
                    model={model}
                  />
                ))}
              </tbody>
            </table>
          )}
          {flagsLoaded && counts.all > 0 && (
            <CardNote>
              Levels only go up. Seen: it entered an agent&rsquo;s context.
              Attempted: a check stopped it leaving. Reported: a monitored
              service saw it presented.
            </CardNote>
          )}
        </Card>
      </Columns>
    </div>
  );
}

function Row({
  row,
  seen,
  model,
}: {
  row: CredentialRow;
  seen: ConsoleAgent[];
  model: ConsoleModel;
}) {
  const { flag } = row;
  const owner =
    flag && !isShared(flag) ? findAgent(model, flag.owner_agent_id) : undefined;
  const first = flag ? firstEvidence(flag) : undefined;
  const decision = decisionLines(row, model.now);
  return (
    <tr className={`cred-row cred-${row.state}`}>
      <td data-label="Key">
        <span className="id cred-key">{row.key}</span>
        <span className="table-sub id cred-ref">{row.ref}</span>
      </td>
      <td data-label="Owner">
        {!flag || isShared(flag) ? (
          "Shared"
        ) : owner ? (
          <a className="text-link id" href={agentHref(owner.key)}>
            {owner.name}
          </a>
        ) : (
          <span className="id">{flag.owner_agent_id}</span>
        )}
      </td>
      <td data-label="Level">{row.level ? <Level level={row.level} /> : ""}</td>
      <td data-label="Seen by">
        {seen.length ? (
          <span className="cred-seen">
            {seen.slice(0, SEEN_CAP).map((agent, index) => (
              <span key={agent.key}>
                {index > 0 && ", "}
                <a className="text-link id" href={agentHref(agent.key)}>
                  {agent.name}
                </a>
              </span>
            ))}
            {seen.length > SEEN_CAP && ` and ${seen.length - SEEN_CAP} more`}
          </span>
        ) : (
          <span className="cred-quiet">Not in the loaded events</span>
        )}
        {flag && (
          <span className="table-sub">
            Evidence from {plural(flag.instance_count, "agent")}
          </span>
        )}
      </td>
      <td data-label="First evidence">
        {first ? (
          <>
            {evidenceSentence(
              first.evidence,
              evidenceAgent(model, first.evidence)?.name,
            )}
            <span className="table-sub">
              {[capital(first.level), dayTime(first.at, model.now)]
                .filter(Boolean)
                .join(", ")}
            </span>
          </>
        ) : (
          ""
        )}
      </td>
      <td data-label="Decision" className="is-end">
        {flag && row.state === "open" ? (
          <FlagActions flag={flag} />
        ) : decision ? (
          <span className="cred-decision">
            <span className="cred-decision-title">{decision.title}</span>
            {decision.detail && (
              <span className="table-sub">{decision.detail}</span>
            )}
            {decision.note && (
              <span className="table-sub">{decision.note}</span>
            )}
          </span>
        ) : null}
      </td>
    </tr>
  );
}

function capital(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

/** A level as a word, with a three-step track that shows how far it went. */
function Level({ level }: { level: FlagLevel }) {
  const rank = levelRank(level);
  return (
    <span className="cred-level">
      <Pill tone={rank > 1 ? "flag" : "neutral"}>{capital(level)}</Pill>
      <span className="cred-track" aria-hidden="true">
        {LEVELS.map((step, index) => (
          <span
            key={step}
            className={index < rank ? "cred-step is-on" : "cred-step"}
          />
        ))}
      </span>
    </span>
  );
}
