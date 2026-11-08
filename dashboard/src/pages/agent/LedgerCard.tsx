import { useMemo } from "react";
import type { LedgerEntry, LedgerEvent } from "../../contracts";
import type { ConsoleAgent } from "../../model/console";
import {
  Card,
  CardNote,
  Crossfade,
  DigitRoll,
  EmptyState,
  Figures,
  Notice,
  Pill,
} from "../../ui";
import { ledgerCounts, ledgerRows } from "./ledger";

/**
 * "EXPOSURE LEDGER / WHAT WORKER-07 SAW": the website hero panel's layout,
 * with the agent's real keys. A row's evidence and action crossfade when
 * they change (a key rotated, a flag raised), and only then.
 */
export function LedgerCard({
  agent,
  entries,
  error,
  events,
  nameOf,
}: {
  agent: ConsoleAgent;
  entries?: LedgerEntry[];
  error: string;
  /** This agent's events. */
  events: LedgerEvent[];
  nameOf: (agentId: string) => string | undefined;
}) {
  const burned = agent.state === "burned";
  const rows = useMemo(
    () =>
      entries
        ? ledgerRows(entries, {
            agentId: agent.agentId,
            events,
            burned,
            nameOf,
          })
        : [],
    [entries, agent.agentId, events, burned, nameOf],
  );
  const counts = entries
    ? ledgerCounts(entries, agent.agentId)
    : { seen: 0, plain: 0, owned: 0 };
  return (
    <Card
      className="agent-ledger"
      rule={false}
      title={
        <>
          <span className="ledger-tile" aria-hidden="true">
            B
          </span>
          Exposure ledger
        </>
      }
      sub={`What ${agent.name} saw`}
    >
      {error && (
        <Notice tone="error">
          Its ledger could not be loaded: {error}
          {entries ? " Showing the last answer." : ""}
        </Notice>
      )}
      {!entries ? (
        !error && <Notice>Loading what {agent.name} saw…</Notice>
      ) : (
        <>
          <Figures
            items={[
              { label: "Keys seen", value: <DigitRoll value={counts.seen} /> },
              {
                label: "In plain text",
                value: <DigitRoll value={counts.plain} />,
              },
              { label: "Owned", value: <DigitRoll value={counts.owned} /> },
            ]}
          />
          {rows.length ? (
            <div
              className="ledger-rows"
              role="table"
              aria-label={`Keys ${agent.name} saw`}
            >
              <div className="ledger-row ledger-row-head cap" role="row">
                <span role="columnheader">Credential / source</span>
                <span role="columnheader">Evidence</span>
                <span role="columnheader">Action</span>
              </div>
              {rows.map((row) => (
                <div className="ledger-row" role="row" key={row.id}>
                  <span className="ledger-cred" role="cell">
                    <span
                      className={`ledger-dot dot-${row.dot}`}
                      aria-hidden="true"
                    />
                    <span>
                      <span className={`ledger-name${row.mono ? " id" : ""}`}>
                        {row.name}
                      </span>
                      <span className="ledger-src">{row.source}</span>
                    </span>
                  </span>
                  <span
                    className={`ledger-ev${row.hot ? " is-hot" : ""}`}
                    role="cell"
                  >
                    <Crossfade id={row.evidence}>{row.evidence}</Crossfade>
                  </span>
                  <span className="ledger-act" role="cell">
                    <Crossfade id={`${row.action.tone}:${row.action.text}`}>
                      <Pill tone={row.action.tone}>{row.action.text}</Pill>
                    </Crossfade>
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState>
              {burned
                ? `${agent.name} saw no keys before it was burned.`
                : `No keys yet. Keys appear here as ${agent.name} reads them.`}
            </EmptyState>
          )}
        </>
      )}
      <CardNote>
        Burn keeps fingerprints, never values. Owned keys are this agent&rsquo;s
        own copies; shared keys belong to a team and are never rotated without
        their owner.
      </CardNote>
    </Card>
  );
}
