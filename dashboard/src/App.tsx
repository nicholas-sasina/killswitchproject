import { useCallback, useMemo, useState } from "react";
import { createApi, readConfig, type ApiConfig } from "./api";
import type { AgentState, BurnResult } from "./contracts";
import { newestEvents } from "./events";
import { MockBanner, mockDataReducer } from "./MockBanner";
import { useEventFeed } from "./polling";
import { ErrorMessage, Flame } from "./components/common";
import { AgentCard } from "./components/AgentCard";
import { BurnDialog } from "./components/BurnDialog";
import { BurnOutcome } from "./components/BurnOutcome";
import { Ledger } from "./components/Ledger";
import { EventFeed } from "./components/EventFeed";
import { BlastTimeline } from "./components/BlastTimeline";
import { LeakTest } from "./components/LeakTest";

export function App({ config = readConfig() }: { config?: ApiConfig }) {
  const [mock, setMock] = useState(false);
  const observeResponse = useCallback(
    (headers: Headers) =>
      setMock((current) => mockDataReducer(current, headers)),
    [],
  );
  const api = useMemo(
    () => createApi(config, observeResponse),
    [config.burnUrl, config.demoToken, config.leakTestUrl, observeResponse],
  );
  const configured = Boolean(config.burnUrl && config.demoToken);
  const { state, error, updatedAt } = useEventFeed(api, configured);
  const events = useMemo(() => newestEvents(state), [state]);
  const [selectedId, setSelectedId] = useState("");
  const [confirmAgent, setConfirmAgent] = useState<AgentState>();
  const [busy, setBusy] = useState(false);
  const [burnError, setBurnError] = useState("");
  const [burnResults, setBurnResults] = useState<
    Record<string, { result: BurnResult; sessionId: AgentState["session_id"] }>
  >({});
  const [refresh, setRefresh] = useState(0);
  const selectedAgent =
    state.agents.find((agent) => agent.agent_id === selectedId) ??
    state.agents[0];
  function resultFor(agent: AgentState) {
    const record = burnResults[agent.agent_id];
    return record?.sessionId === agent.session_id ? record?.result : undefined;
  }
  const selectedResult = selectedAgent ? resultFor(selectedAgent) : undefined;
  const burnedId =
    selectedAgent &&
    (selectedAgent.status === "revoked" || selectedResult?.revoked)
      ? selectedAgent.agent_id
      : undefined;
  async function burn(reason: string) {
    if (!confirmAgent || busy) return;
    const id = confirmAgent.agent_id;
    setBusy(true);
    setBurnError("");
    try {
      const result = await api.burn({
        agent_id: id,
        ...(reason.trim() ? { reason: reason.trim() } : {}),
      });
      setBurnResults((current) => ({
        ...current,
        [id]: { result, sessionId: confirmAgent.session_id },
      }));
      setSelectedId(id);
      setRefresh((value) => value + 1);
      setConfirmAgent(undefined);
    } catch (error) {
      setBurnError(
        `${error instanceof Error ? error.message : "Burn request failed."} Outcome not confirmed. Check agent status before retrying.`,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <MockBanner visible={mock} />
      <div className="app-shell">
        <header className="site-header">
          <a href="/" className="brand" aria-label="Burn home">
            <Flame />
            <span>BURN</span>
          </a>
          <div
            className={`connection ${error ? "connection-error" : updatedAt ? "connection-live" : ""}`}
          >
            <span aria-hidden="true" />
            {error
              ? "Connection interrupted"
              : updatedAt
                ? "Connected · polling every second"
                : "Waiting for connection"}
          </div>
        </header>
        <main>
          <section className="hero">
            <div>
              <p className="eyebrow">Agent containment console</p>
              <h1>
                Revoke the agent.
                <br />
                <span>Contain what it saw.</span>
              </h1>
              <p>
                One action to revoke access, rotate owned credentials,
                <br className="desktop-break" /> and flag shared exposure for
                review.
              </p>
            </div>
            <dl className="summary">
              <div>
                <dt>Agents</dt>
                <dd>{updatedAt ? state.agents.length : "—"}</dd>
              </div>
              <div>
                <dt>Active</dt>
                <dd className="green-text">
                  {updatedAt
                    ? state.agents.filter((agent) => agent.status === "active")
                        .length
                    : "—"}
                </dd>
              </div>
              <div>
                <dt>Revoked</dt>
                <dd>
                  {updatedAt
                    ? state.agents.filter((agent) => agent.status === "revoked")
                        .length
                    : "—"}
                </dd>
              </div>
            </dl>
          </section>
          {!configured && (
            <section className="setup panel" role="status">
              <h2>Connect the dashboard</h2>
              <p>
                Set <code>VITE_BURN_API_URL</code> and{" "}
                <code>VITE_BURN_DEMO_TOKEN</code> in{" "}
                <code>dashboard/.env.local</code>, then restart Vite. Use{" "}
                <code>.env.example</code> for the configuration fields.
              </p>
            </section>
          )}
          <ErrorMessage
            message={
              error
                ? `${error} The display retains the last received data while reconnecting.`
                : ""
            }
          />
          <section aria-labelledby="agents-title" className="agents-section">
            <div className="section-heading">
              <h2 id="agents-title">Agents</h2>
              <span className="muted">
                {updatedAt
                  ? `Last received ${updatedAt.toLocaleTimeString([], { hour12: false })}`
                  : "Awaiting API data"}
              </span>
            </div>
            {state.agents.length ? (
              <div className="agent-grid">
                {state.agents.map((agent) => (
                  <AgentCard
                    key={agent.agent_id}
                    agent={agent}
                    selected={selectedAgent?.agent_id === agent.agent_id}
                    busy={busy || Boolean(error)}
                    burned={Boolean(resultFor(agent)?.revoked)}
                    onSelect={() => setSelectedId(agent.agent_id)}
                    onBurn={() => {
                      setBurnError("");
                      setConfirmAgent(agent);
                    }}
                  />
                ))}
              </div>
            ) : (
              <div className="empty panel">
                {updatedAt
                  ? "No agents in the current scenario. Start a scenario to see its agents here."
                  : "Agent cards will appear when the API connects."}
              </div>
            )}
          </section>
          {selectedResult && <BurnOutcome result={selectedResult} />}
          <div className="workspace-grid">
            <div className="detail-column">
              <Ledger
                api={api}
                agentId={selectedAgent?.agent_id}
                refresh={refresh}
              />
              <BlastTimeline api={api} agentId={burnedId} />
            </div>
            <EventFeed events={events} />
          </div>
          <LeakTest
            api={api}
            agents={state.agents}
            configured={configured && Boolean(config.leakTestUrl)}
          />
        </main>
        <footer>
          <span>Burn · Agent containment</span>
          <span>Live API data · credential references only</span>
        </footer>
      </div>
      {confirmAgent && (
        <BurnDialog
          key={confirmAgent.agent_id}
          agent={confirmAgent}
          busy={busy}
          error={burnError}
          onCancel={() => setConfirmAgent(undefined)}
          onConfirm={(reason) => void burn(reason)}
        />
      )}
    </>
  );
}
