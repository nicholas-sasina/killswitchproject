/**
 * An incident row, opened in place: what Burn did, what the agent saw (its
 * ledger, asked for the first time the row opens), the keys still open with
 * their Rotate and Dismiss, its replacement, and a link to the agent page.
 */
import { useMemo } from "react";
import type { Flag, LedgerEvent } from "../../contracts";
import { FlagActions } from "../../features/flags/FlagActions";
import { flagKey, flagTag } from "../../features/flags/describe";
import { findAgent, type ConsoleModel, type Incident } from "../../model";
import { agentHref } from "../../router";
import { Notice } from "../../ui";
import { ledgerRows } from "../agent/ledger";
import { agentEvents, replacementOf } from "../agent/story";
import { didItems } from "./describe";
import type { LedgerState } from "./useLedgers";

/** Ledger rows shown here before "N more on its page". */
const SAW_CAP = 6;

export function IncidentDetail({
  incident,
  events,
  model,
  flags,
  ledger,
  onRetry,
}: {
  incident: Incident;
  /** Every loaded event, newest first. */
  events: LedgerEvent[];
  model: ConsoleModel;
  flags: Flag[];
  ledger: LedgerState | undefined;
  onRetry: () => void;
}) {
  const { agent } = incident;
  const own = useMemo(
    () => agentEvents(events, agent, model),
    [events, agent, model],
  );
  const did = didItems(incident, own, flags);
  const saw = useMemo(
    () =>
      ledger?.entries
        ? ledgerRows(ledger.entries, {
            agentId: agent.agentId,
            events: own,
            burned: true,
            nameOf: (agentId) => findAgent(model, agentId)?.name,
          })
        : [],
    [ledger?.entries, agent.agentId, own, model],
  );
  const open = flags.filter(
    (flag) =>
      flag.state === "open" && incident.openKeys.includes(flag.secret_ref),
  );
  const handover = replacementOf(agent, model);
  const href = agentHref(agent.sessionId ?? agent.key);

  return (
    <div className="inc-detail-body">
      <div className="inc-detail-grid">
        <section className="inc-part">
          <h3 className="cap inc-part-title">What Burn did</h3>
          <ul className="inc-part-list">
            {did.map((item, index) => (
              <li key={index}>
                {item.key && (
                  <>
                    <span className="id">{item.key}</span>{" "}
                  </>
                )}
                {item.text}
              </li>
            ))}
          </ul>
        </section>

        <section className="inc-part">
          <h3 className="cap inc-part-title">What it saw</h3>
          {ledger?.error ? (
            <>
              <Notice tone="error">
                Its ledger could not be loaded: {ledger.error}
              </Notice>
              <button
                type="button"
                className="btn btn-sm inc-retry"
                onClick={onRetry}
              >
                Try again
              </button>
            </>
          ) : !ledger?.entries ? (
            <p className="inc-quiet">Loading what {agent.name} saw…</p>
          ) : !saw.length ? (
            <p className="inc-quiet">No key reached it.</p>
          ) : (
            <ul className="inc-part-list inc-saw">
              {saw.slice(0, SAW_CAP).map((row) => (
                <li key={row.id}>
                  <span className={row.mono ? "id inc-saw-key" : "inc-saw-key"}>
                    {row.name}
                  </span>
                  <span
                    className={row.hot ? "inc-saw-ev is-hot" : "inc-saw-ev"}
                  >
                    {row.evidence}
                  </span>
                  <span className="inc-saw-from">{row.source}</span>
                </li>
              ))}
              {saw.length > SAW_CAP && (
                <li className="inc-quiet">
                  {saw.length - SAW_CAP} more on its page
                </li>
              )}
            </ul>
          )}
        </section>

        <section className="inc-part">
          <h3 className="cap inc-part-title">Still open</h3>
          {open.length ? (
            <ul className="inc-part-list inc-open-keys">
              {open.map((flag) => (
                <li key={flag.secret_ref}>
                  <span className="cap inc-open-tag">{flagTag(flag)}</span>
                  <span className="id inc-open-name">{flagKey(flag)}</span>
                  <FlagActions flag={flag} variant="compact" />
                </li>
              ))}
            </ul>
          ) : (
            <p className="inc-quiet">Nothing is open.</p>
          )}
        </section>

        <section className="inc-part">
          <h3 className="cap inc-part-title">Replacement</h3>
          {handover ? (
            <p>
              {handover.other ? (
                <>
                  <a
                    className="text-link id"
                    href={agentHref(handover.other.key)}
                  >
                    {handover.other.name}
                  </a>{" "}
                  took over
                </>
              ) : (
                handover.headline
              )}
            </p>
          ) : (
            <p className="inc-quiet">Its instance record isn&rsquo;t loaded.</p>
          )}
        </section>
      </div>
      <a className="text-link inc-detail-link" href={href}>
        Open {agent.name}
      </a>
    </div>
  );
}
