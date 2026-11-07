import type { AgentState } from "../contracts";
import { Flame, Status } from "./common";

export function AgentCard({
  agent,
  selected,
  busy,
  burned,
  onSelect,
  onBurn,
}: {
  agent: AgentState;
  selected: boolean;
  busy: boolean;
  burned: boolean;
  onSelect: () => void;
  onBurn: () => void;
}) {
  const status = burned ? "revoked" : agent.status;
  return (
    <article className={`agent-card ${selected ? "selected" : ""}`}>
      <button
        className="agent-select"
        onClick={onSelect}
        aria-pressed={selected}
        aria-label={`Inspect ${agent.agent_id}`}
      >
        <div className="card-top">
          <span className="eyebrow">{agent.role || "Agent"}</span>
          <Status value={status} />
        </div>
        <h2>{agent.agent_id}</h2>
        <div className="session">
          <span>Session</span>
          <code title={agent.session_id ?? undefined}>
            {agent.session_id || "No active session"}
          </code>
        </div>
        <div className="exposure">
          <strong>{agent.exposures ?? "—"}</strong>
          <span>credentials seen</span>
          <span className="inspect">View ledger ↗</span>
        </div>
      </button>
      <button
        className="burn-button"
        onClick={onBurn}
        disabled={busy || status === "revoked"}
        aria-label={`Burn ${agent.agent_id}`}
      >
        <Flame />
        {status === "revoked" ? "Revoked" : "Burn agent"}
      </button>
    </article>
  );
}
