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
  TIER_LABEL,
  bySeverity,
  countByTier,
  toAlerts,
  type Alert,
  type Tier,
} from "../alerts";
import type { LedgerEvent } from "../contracts";
import { Timestamp } from "./common";

const TALLY_LABEL: Record<Tier, string> = {
  crit: "Critical",
  high: "High",
  warn: "Review",
  info: "Noted",
};

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

function AlertDetail({ alert }: { alert: Alert }) {
  const { event } = alert;
  return (
    <section
      className="panel alert-detail"
      aria-labelledby="alert-detail-title"
    >
      <div className="panel-heading">
        <div>
          <p className="eyebrow">
            <span className={`sev sev-${alert.tier}`}>
              [{TIER_LABEL[alert.tier]}]
            </span>{" "}
            Alert detail
          </p>
          <h2 id="alert-detail-title">{alert.title}</h2>
        </div>
      </div>

      <div className="explain">
        <p>
          <strong>What this proves.</strong> {alert.why}
        </p>
      </div>
      <div className="explain">
        <p>
          <strong>What to do.</strong> {alert.next}
        </p>
      </div>

      {event.detail && (
        <div className="reported">
          <span className="eyebrow">Reported by</span>
          <p>
            {event.source.replaceAll("_", " ")}: <code>{event.detail}</code>
          </p>
        </div>
      )}

      <dl>
        <Fact label="Agent" value={event.agent_id} mono />
        <Fact label="Role" value={event.role} />
        <Fact label="Seen at" value={new Date(event.ts).toLocaleString()} />
        <Fact label="Source" value={event.source.replaceAll("_", " ")} />
        <Fact label="Action" value={event.action.replaceAll("_", " ")} />
        <Fact label="Tool" value={event.tool} mono />
        <Fact label="Destination" value={event.destination} mono />
        <Fact label="Encoding" value={event.encoding} />
        <Fact label="Passed to" value={event.target_agent_id} mono />
        <Fact label="Credential" value={event.secret_ref} mono />
        <Fact label="Fingerprint" value={event.fingerprint} mono />
        <Fact label="Type" value={event.secret_type?.replaceAll("_", " ")} />
        <Fact label="Owner" value={event.owner_agent_id || event.owner} mono />
        <Fact label="Flag level" value={event.flag_level} />
        <Fact label="Session" value={event.session_id} mono />
        <Fact label="Parent" value={event.parent_session} mono />
        <Fact label="Event" value={event.event_id} mono />
      </dl>

      <p className="panel-footnote">
        Credential references and fingerprints only. Raw credential values are
        never returned to the browser.
      </p>
    </section>
  );
}

export function AlertsView({ events }: { events: LedgerEvent[] }) {
  const alerts = useMemo(() => bySeverity(toAlerts(events)), [events]);
  const counts = useMemo(() => countByTier(alerts), [alerts]);
  const [hidden, setHidden] = useState<Tier[]>([]);
  const [agentFilter, setAgentFilter] = useState("");
  const [selectedId, setSelectedId] = useState("");

  const agents = useMemo(
    () => [...new Set(alerts.map((alert) => alert.event.agent_id))].sort(),
    [alerts],
  );
  const shown = useMemo(
    () =>
      alerts.filter(
        (alert) =>
          !hidden.includes(alert.tier) &&
          (!agentFilter || alert.event.agent_id === agentFilter),
      ),
    [alerts, hidden, agentFilter],
  );
  // Keep a selection valid as the stream moves underneath it.
  const selected =
    shown.find((alert) => alert.event.event_id === selectedId) ?? shown[0];

  function toggleTier(tier: Tier) {
    setHidden((current) =>
      current.includes(tier)
        ? current.filter((value) => value !== tier)
        : [...current, tier],
    );
  }

  return (
    <>
      <div className="section-heading">
        <h2 id="alerts-title">Alerts</h2>
        <span className="muted">
          {alerts.length
            ? `${alerts.length} in the current stream`
            : "Nothing needs attention"}
        </span>
      </div>

      {/* Tallies double as filters: the readout you look at is the control
          you use, so there is no separate filter bar for severity. */}
      <div className="alert-ribbon">
        {TIERS.map((tier) => (
          <button
            key={tier}
            type="button"
            className="alert-tally"
            data-tier={tier}
            data-zero={counts[tier] === 0}
            aria-pressed={!hidden.includes(tier)}
            onClick={() => toggleTier(tier)}
            aria-label={`${TALLY_LABEL[tier]} alerts: ${counts[tier]}. ${
              hidden.includes(tier) ? "Hidden" : "Shown"
            }`}
          >
            <span className="count">{counts[tier]}</span>
            <span className="label">{TALLY_LABEL[tier]}</span>
          </button>
        ))}
      </div>

      <div className="alert-layout">
        <section className="panel" aria-labelledby="queue-title">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Needs attention</p>
              <h2 id="queue-title">Alert queue</h2>
            </div>
            <span className="meta">
              Most serious first · {shown.length} shown
            </span>
          </div>

          <div className="alert-filters">
            <span className="eyebrow">Agent</span>
            <button
              type="button"
              className="chip"
              aria-pressed={agentFilter === ""}
              onClick={() => setAgentFilter("")}
            >
              All
            </button>
            {agents.map((agent) => (
              <button
                key={agent}
                type="button"
                className="chip"
                aria-pressed={agentFilter === agent}
                onClick={() => setAgentFilter(agent)}
              >
                {agent}
              </button>
            ))}
          </div>

          {!shown.length ? (
            <div className="empty">
              <p>
                {alerts.length
                  ? "No alerts match these filters. Re-enable a severity above, or clear the agent filter."
                  : "No alerts yet. Blocked handoffs, blocked egress, canary use and replayed credentials appear here as they happen."}
              </p>
            </div>
          ) : (
            <ol className="alert-list">
              {shown.map((alert, index) => (
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
                      {String(index + 1).padStart(3, "0")}
                    </span>
                    <span className="alert-main">
                      <span className="alert-head">
                        <span className={`sev sev-${alert.tier}`}>
                          [{TIER_LABEL[alert.tier]}]
                        </span>
                        <span className="alert-title">{alert.title}</span>
                        <span className="alert-when">
                          <Timestamp value={alert.event.ts} />
                        </span>
                      </span>
                      <span className="alert-sub">
                        {alert.event.agent_id}
                        <span className="sep">·</span>
                        {alert.event.source.replaceAll("_", " ")}
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
        </section>

        {selected ? (
          <AlertDetail alert={selected} />
        ) : (
          <section className="panel" aria-label="Alert detail">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Alert detail</p>
                <h2>Nothing selected</h2>
              </div>
            </div>
            <p className="empty">
              Select an alert to see every field recorded for it, what it
              proves, and what to do about it.
            </p>
          </section>
        )}
      </div>
    </>
  );
}
