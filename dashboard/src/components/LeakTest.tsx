import { useCallback, useState, type FormEvent } from "react";
import type { BurnApi } from "../api";
import type { AgentState, LeakTestResult } from "../contracts";
import { usePolling } from "../polling";
import { ErrorMessage } from "./common";

export function LeakTest({
  api,
  agents,
  configured,
}: {
  api: BurnApi;
  agents: AgentState[];
  configured: boolean;
}) {
  const load = useCallback(
    (signal: AbortSignal) => api.encodings(signal),
    [api],
  );
  const { data, error: encodingError } = usePolling(
    configured ? load : null,
    30_000,
  );
  const [agentId, setAgentId] = useState("");
  const [encoding, setEncoding] = useState("");
  const [payload, setPayload] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<LeakTestResult>();
  const [error, setError] = useState("");
  const selectedAgent = agents.some((agent) => agent.agent_id === agentId)
    ? agentId
    : (agents[0]?.agent_id ?? "");
  const encodings = data?.encodings ?? [];
  const available = configured && encodings.length > 0 && !encodingError;
  const selectedEncoding = encodings.includes(encoding)
    ? encoding
    : (encodings[0] ?? "");
  async function send(event: FormEvent) {
    event.preventDefault();
    if (busy || !available || !selectedAgent || !selectedEncoding) return;
    const body = {
      agent_id: selectedAgent,
      encoding: selectedEncoding,
      ...(payload ? { free_text: payload } : {}),
    };
    setPayload("");
    setResult(undefined);
    setError("");
    setBusy(true);
    try {
      setResult(await api.leakTest(body));
    } catch (error) {
      setError(error instanceof Error ? error.message : "Leak test failed.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel test-panel" aria-labelledby="test-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow test-label">TEST TOOL</p>
          <h2 id="test-title">Try the exit check</h2>
        </div>
        <span className="muted">Sends through the agent’s egress proxy</span>
      </div>
      <p className="test-description">
        Choose an encoding to test a credential from the selected agent’s
        ledger, or send your own test payload.
      </p>
      {!configured && (
        <p className="empty">
          Set VITE_LEAKTEST_URL in dashboard/.env.local and restart Vite to
          enable this tool.
        </p>
      )}
      {configured && encodingError && (
        <p className="service-note" role="status">
          Leak-test service unavailable. This test tool will retry automatically
          every 30 seconds.
        </p>
      )}
      <form onSubmit={(event) => void send(event)}>
        <div className="test-controls">
          <label>
            Agent
            <select
              value={selectedAgent}
              disabled={busy || !available}
              onChange={(event) => {
                setAgentId(event.target.value);
                setResult(undefined);
              }}
            >
              <option value="" disabled>
                Select an agent
              </option>
              {agents.map((agent) => (
                <option key={agent.agent_id} value={agent.agent_id}>
                  {agent.agent_id} · {agent.status}
                </option>
              ))}
            </select>
          </label>
          <label>
            Encoding
            <select
              value={selectedEncoding}
              disabled={busy || !available}
              onChange={(event) => {
                setEncoding(event.target.value);
                setResult(undefined);
              }}
            >
              <option value="" disabled>
                {encodingError ? "Encodings unavailable" : "Choose encoding"}
              </option>
              {encodings.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <button
            className="secondary-button send-button"
            disabled={busy || !available || !selectedAgent || !selectedEncoding}
          >
            {busy ? "Sending…" : "Send test ↗"}
          </button>
        </div>
        <label htmlFor="test-payload">
          Free-text payload{" "}
          <span className="muted">
            (optional · leave blank to use a ledger credential)
          </span>
        </label>
        <textarea
          id="test-payload"
          rows={2}
          value={payload}
          onChange={(event) => setPayload(event.target.value)}
          disabled={busy || !available}
          autoComplete="off"
          spellCheck={false}
          placeholder="Enter a test payload. It is cleared when sent."
        />
      </form>
      <ErrorMessage message={error} />
      {result && (
        <div
          className={`test-result ${result.blocked ? "test-blocked" : "test-delivered"}`}
          role="status"
        >
          <strong>{result.blocked ? "⊘ Blocked" : "↗ Delivered"}</strong>
          <span>
            {result.reason ||
              (result.blocked
                ? "The proxy stopped this test request."
                : "The proxy allowed this test request.")}
          </span>
          <small>
            {result.encoding}
            {result.requests_sent !== undefined
              ? ` · ${result.requests_sent} requests sent`
              : ""}
            {result.destination ? ` · ${result.destination}` : ""}
          </small>
          {result.secret_ref && <code>{result.secret_ref}</code>}
        </div>
      )}
    </section>
  );
}
