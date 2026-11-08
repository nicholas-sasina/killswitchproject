import { useCallback, useMemo, useState } from "react";
import { createApi, readConfig, type ApiConfig } from "./api";
import { attentionCount, toAlerts } from "./alerts";
import type { AgentState, BurnResult } from "./contracts";
import { newestEvents } from "./events";
import { MockBanner, mockDataReducer } from "./MockBanner";
import { useEventFeed } from "./polling";
import { hrefFor, useView, VIEWS, type View } from "./router";
import { useSystemTheme } from "./theme";
import { ErrorMessage } from "./components/common";
import { Brand } from "./components/Brand";
import { AgentCard } from "./components/AgentCard";
import { AlertsView } from "./components/AlertsView";
import { BurnDialog } from "./components/BurnDialog";
import { BurnOutcome } from "./components/BurnOutcome";
import { Ledger } from "./components/Ledger";
import { EventFeed } from "./components/EventFeed";
import { BlastTimeline } from "./components/BlastTimeline";
import { PageHead } from "./components/PageHead";
import { SwarmPanel } from "./components/SwarmPanel";

const NAV: Record<View, { label: string; hint: string }> = {
  overview: { label: "Overview", hint: "Agents and containment" },
  fleet: { label: "Fleet", hint: "Roles and instances" },
  alerts: { label: "Alerts", hint: "What needs attention" },
};

export function App({ config = readConfig() }: { config?: ApiConfig }) {
  const [mock, setMock] = useState(false);
  const observeResponse = useCallback(
    (headers: Headers) =>
      setMock((current) => mockDataReducer(current, headers)),
    [],
  );
  const api = useMemo(
    () => createApi(config, observeResponse),
    [config.burnUrl, config.demoToken, observeResponse],
  );
  const configured = Boolean(config.burnUrl && config.demoToken);
  const { state, error, updatedAt } = useEventFeed(api, configured);
  const events = useMemo(() => newestEvents(state), [state]);
  const view = useView();
  // Applies the operating system's light or dark setting and follows it.
  useSystemTheme();
  // The navigation marker counts anything above "noted", so the operator can
  // stay on another view and still know the queue is filling.
  const pending = useMemo(() => attentionCount(toAlerts(events)), [events]);
  const [selectedId, setSelectedId] = useState("");
  const [confirmAgent, setConfirmAgent] = useState<AgentState>();
  const [busy, setBusy] = useState(false);
  const [burnError, setBurnError] = useState("");
  const [burnResults, setBurnResults] = useState<
    Record<string, { result: BurnResult; sessionId: AgentState["session_id"] }>
  >({});
  const [refresh, setRefresh] = useState(0);
  const activeCount = state.agents.filter(
    (agent) => agent.status === "active",
  ).length;
  const revokedCount = state.agents.filter(
    (agent) => agent.status === "revoked",
  ).length;
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
      <div className="app-frame">
        <aside className="sidebar">
          <div className="sidebar-brand">
            <Brand />
          </div>
          <nav className="side-nav" aria-label="Views">
            {VIEWS.map((name) => (
              <a
                key={name}
                href={hrefFor(name)}
                className="side-link"
                aria-current={view === name ? "page" : undefined}
              >
                <span className="side-label">
                  {NAV[name].label}
                  {name === "alerts" && updatedAt && pending > 0 && (
                    <b aria-label={`${pending} needing attention`}>{pending}</b>
                  )}
                </span>
                <span className="side-hint">{NAV[name].hint}</span>
              </a>
            ))}
          </nav>
          <div className="sidebar-foot">
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
            <p className="sidebar-note">
              Credential references only. Raw values never reach the browser.
            </p>
          </div>
        </aside>

        <div className="content">
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
          <main>
            {view === "alerts" ? (
              <>
                <PageHead
                  title="Alerts"
                  description="Only the events that need an operator, most serious first. Each one explains what it proves and what to do about it."
                  attention={pending > 0}
                />
                <AlertsView events={events} />
              </>
            ) : view === "fleet" ? (
              <>
                <PageHead
                  title="Fleet"
                  description="Roles, join tokens and instance lineage. Add an agent to create a dashboard-managed role, or burn a whole role at once."
                />
                <SwarmPanel
                  key={`${config.burnUrl}:${config.demoToken}`}
                  api={api}
                  enabled={configured}
                />
              </>
            ) : (
              <>
                <PageHead
                  title="Overview"
                  description="Live agent status, the credentials each agent has seen, and what containment did."
                  attention={revokedCount > 0 || pending > 0}
                  figures={[
                    {
                      label: "Agents",
                      value: updatedAt ? state.agents.length : "—",
                    },
                    {
                      label: "Active",
                      value: updatedAt ? activeCount : "—",
                      tone: activeCount ? "nominal" : undefined,
                    },
                    {
                      label: "Revoked",
                      value: updatedAt ? revokedCount : "—",
                      tone: revokedCount ? "ember" : undefined,
                    },
                    {
                      label: "Alerts",
                      value: updatedAt ? pending : "—",
                      tone: pending ? "caution" : undefined,
                    },
                  ]}
                  meta={
                    updatedAt
                      ? `Last received ${updatedAt.toLocaleTimeString([], { hour12: false })}`
                      : "Awaiting API data"
                  }
                />
                <div className="workspace-grid">
                  <div className="detail-column">
                    <section
                      aria-labelledby="agents-title"
                      className="agents-section"
                    >
                      <div className="section-heading">
                        <h2 id="agents-title">Agents</h2>
                        <span className="muted">
                          {state.agents.length
                            ? `${state.agents.length} in this scenario`
                            : ""}
                        </span>
                      </div>
                      {state.agents.length ? (
                        <div className="agent-grid">
                          {state.agents.map((agent) => (
                            <AgentCard
                              key={agent.agent_id}
                              agent={agent}
                              selected={
                                selectedAgent?.agent_id === agent.agent_id
                              }
                              busy={busy || Boolean(error)}
                              result={resultFor(agent)}
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
                    <div className="forensics">
                      <div className="section-heading">
                        <h2>Containment detail</h2>
                        <span className="muted">
                          {selectedAgent?.agent_id ?? ""}
                        </span>
                      </div>
                      <Ledger
                        api={api}
                        agentId={selectedAgent?.agent_id}
                        refresh={refresh}
                      />
                      <BlastTimeline api={api} agentId={burnedId} />
                    </div>
                  </div>
                  <EventFeed events={events} />
                </div>
              </>
            )}
          </main>
        </div>
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
