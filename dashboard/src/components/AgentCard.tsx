import { useEffect, useState } from "react";
import type { AgentState, BurnResult } from "../contracts";
import { BurnOutcome } from "./BurnOutcome";
import { Flame, Status } from "./common";

export function AgentCard({
  agent,
  selected,
  busy,
  result,
  onSelect,
  onBurn,
}: {
  agent: AgentState;
  selected: boolean;
  busy: boolean;
  result?: BurnResult;
  onSelect: () => void;
  onBurn: () => void;
}) {
  const [recentBurn, setRecentBurn] = useState(false);
  useEffect(() => {
    setRecentBurn(Boolean(result));
    if (!result) return;
    const timer = window.setTimeout(() => setRecentBurn(false), 10_000);
    return () => window.clearTimeout(timer);
  }, [result]);
  const status = result?.revoked ? "revoked" : agent.status;
  return (
    <article
      className={`agent-card ${selected ? "selected" : ""} ${recentBurn ? "agent-card-recent" : ""}`}
    >
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
      {recentBurn && result && <BurnOutcome result={result} compact />}
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
