/**
 * The alerts location: only events that need an operator, and everything
 * known about the one selected.
 *
 * Separate from the overview on purpose. The overview answers "what are my
 * agents doing", which is a stream you watch. This answers "what needs me",
 * which is a queue you work, sorted by what each event proves rather than by
 * when it arrived.
 */
import { useMemo, useState } from "react";
import {
  TIERS,
  TIER_MEANING,
  TIER_NAME,
  bySeverity,
  countByTier,
  toAlerts,
  type Alert,
  type Tier,
} from "../alerts";
import type { LedgerEvent } from "../contracts";
import { Timestamp } from "./common";

/** One row of the facts table, rendered only when the field is present. */
function Fact({
  label,
  value,
  mono = false,
}: {
  label: string;
  value?: string | null;
  mono?: boolean;
}) {
  if (!value) return null;
  return (
    <>
      <dt>{label}</dt>
      <dd>{mono ? <code>{value}</code> : value}</dd>
    </>
  );
}

/** Severity as a colour chip and a word, matching how status is shown. */
function Severity({ tier }: { tier: Tier }) {
  return (
    <span className={`sev sev-${tier}`}>
      <span aria-hidden="true" />
      {TIER_NAME[tier]}
    </span>
  );
}

function AlertDetail({ alert }: { alert: Alert }) {
  const { event } = alert;
  return (
    <section
      className="panel alert-detail"
      aria-labelledby="alert-detail-title"
    >
      <div className="panel-heading">
        <div>
          <Severity tier={alert.tier} />
          <h2 id="alert-detail-title">{alert.title}</h2>
        </div>
      </div>

      <div className="explain">
        <p>
          <strong>What this means.</strong> {alert.why}
        </p>
      </div>
      <div className="explain">
        <p>
          <strong>What to do.</strong> {alert.next}
        </p>
      </div>

      {event.detail && (
        <div className="reported">
          <span className="eyebrow">
            Reported by the {event.source.replaceAll("_", " ")}
          </span>
          <p>
            <code>{event.detail}</code>
          </p>
        </div>
      )}

      <dl>
        <Fact label="Agent" value={event.agent_id} mono />
        <Fact label="Role" value={event.role} />
        <Fact label="Seen at" value={new Date(event.ts).toLocaleString()} />
        <Fact label="Credential" value={event.secret_ref} mono />
        <Fact label="Type" value={event.secret_type?.replaceAll("_", " ")} />
        <Fact label="Owner" value={event.owner_agent_id || event.owner} mono />
        <Fact label="Tool" value={event.tool} mono />
        <Fact label="Destination" value={event.destination} mono />
        <Fact label="Encoding" value={event.encoding} />
        <Fact label="Passed to" value={event.target_agent_id} mono />
        <Fact label="Flag level" value={event.flag_level} />
        <Fact label="Session" value={event.session_id} mono />
        <Fact label="Fingerprint" value={event.fingerprint} mono />
        <Fact label="Event id" value={event.event_id} mono />
      </dl>

      <p className="panel-footnote">
        Credential references and fingerprints only. Raw credential values are
        never sent to the browser.
      </p>
    </section>
  );
}

/** Rows drawn before the list is truncated; a noisy stack can raise hundreds. */
const ROW_CAP = 60;

export function AlertsView({ events }: { events: LedgerEvent[] }) {
  const alerts = useMemo(() => bySeverity(toAlerts(events)), [events]);
  const counts = useMemo(() => countByTier(alerts), [alerts]);
  const [hidden, setHidden] = useState<Tier[]>([]);
  const [agentFilter, setAgentFilter] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [showAll, setShowAll] = useState(false);

  // Agents that actually have alerts, noisiest first, so the one flooding
  // the queue is the first thing you can filter to.
  const agents = useMemo(() => {
    const counts = new Map<string, number>();
    for (const alert of alerts)
      counts.set(
        alert.event.agent_id,
        (counts.get(alert.event.agent_id) ?? 0) + 1,
      );
    return [...counts.entries()].sort(
      (a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
    );
  }, [alerts]);
  const shown = useMemo(
    () =>
      alerts.filter(
        (alert) =>
          !hidden.includes(alert.tier) &&
          (!agentFilter || alert.event.agent_id === agentFilter),
      ),
    [alerts, hidden, agentFilter],
  );
  const visible = showAll ? shown : shown.slice(0, ROW_CAP);
  // Keep a selection valid as the stream moves underneath it.
  const selected =
    shown.find((alert) => alert.event.event_id === selectedId) ?? shown[0];
  const filtering = hidden.length > 0 || agentFilter !== "";

  function toggleTier(tier: Tier) {
    setHidden((current) =>
      current.includes(tier)
        ? current.filter((value) => value !== tier)
        : [...current, tier],
    );
  }

  return (
    <>
      {/* The tallies are the filter. The readout you look at is the control
          you use, so there is no separate severity filter bar. */}
      <div className="alert-ribbon">
        {TIERS.map((tier) => {
          const on = !hidden.includes(tier);
          return (
            <button
              key={tier}
              type="button"
              className="alert-tally"
              data-tier={tier}
              data-zero={counts[tier] === 0}
              aria-pressed={on}
              onClick={() => toggleTier(tier)}
              aria-label={`${TIER_NAME[tier]}: ${counts[tier]}. ${
                on ? "Showing. Select to hide." : "Hidden. Select to show."
              }`}
            >
              <span className="count">{counts[tier]}</span>
              <span className="label">{TIER_NAME[tier]}</span>
              <span className="meaning">{TIER_MEANING[tier]}</span>
            </button>
          );
        })}
      </div>

      <div className="alert-layout">
        <section className="panel" aria-labelledby="queue-title">
          <div className="panel-heading">
            <h2 id="queue-title">
              {shown.length} {shown.length === 1 ? "alert" : "alerts"}
              {filtering ? " shown" : ""}
            </h2>
            <span className="meta">Most serious first</span>
          </div>

          {agents.length > 1 && (
            <div className="alert-filters">
              <label>
                Agent
                <select
                  value={agentFilter}
                  onChange={(event) => setAgentFilter(event.target.value)}
                >
                  <option value="">All agents ({alerts.length})</option>
                  {agents.map(([agent, count]) => (
                    <option key={agent} value={agent}>
                      {agent} ({count})
                    </option>
                  ))}
                </select>
              </label>
              {filtering && (
                <button
                  type="button"
                  className="chip"
                  onClick={() => {
                    setAgentFilter("");
                    setHidden([]);
                  }}
                >
                  Clear filters
                </button>
              )}
            </div>
          )}

          {!shown.length ? (
            <div className="empty">
              <p>
                {alerts.length
                  ? "Nothing matches the current filters. Select a severity above to bring it back."
                  : "Nothing needs attention. Blocked credentials, canary use and replayed keys will show up here as they happen."}
              </p>
            </div>
          ) : (
            <ol className="alert-list">
              {visible.map((alert, index) => (
                <li key={alert.event.event_id}>
                  <button
                    type="button"
                    className="alert-row"
                    data-tier={alert.tier}
                    aria-pressed={
                      alert.event.event_id === selected?.event.event_id
                    }
                    onClick={() => setSelectedId(alert.event.event_id)}
                  >
                    <span className="alert-index" aria-hidden="true">
                      {index + 1}
                    </span>
                    <span className="alert-main">
                      <span className="alert-head">
                        <Severity tier={alert.tier} />
                        <span className="alert-title">{alert.title}</span>
                        <span className="alert-when">
                          <Timestamp value={alert.event.ts} />
                        </span>
                      </span>
                      <span className="alert-sub">
                        {alert.event.agent_id}
                        {alert.event.secret_ref && (
                          <>
                            <span className="sep">·</span>
                            {alert.event.secret_ref}
                          </>
                        )}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          )}
          {shown.length > ROW_CAP && (
            <div className="trail-more">
              <button
                type="button"
                className="secondary-button"
                onClick={() => setShowAll((current) => !current)}
              >
                {showAll ? `Show first ${ROW_CAP}` : `Show all ${shown.length}`}
              </button>
            </div>
          )}
        </section>

        {selected ? (
          <AlertDetail alert={selected} />
        ) : (
          <section className="panel" aria-label="Alert detail">
            <div className="panel-heading">
              <h2>No alert selected</h2>
            </div>
            <p className="empty">
              Select an alert to see what it means, what to do about it, and
              every detail recorded for it.
            </p>
          </section>
        )}
      </div>
    </>
  );
}
