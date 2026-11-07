import { useCallback } from "react";
import type { BurnApi } from "../api";
import { usePolling } from "../polling";
import { ErrorMessage, Status } from "./common";

export function Ledger({
  api,
  agentId,
  refresh,
}: {
  api: BurnApi;
  agentId?: string;
  refresh: number;
}) {
  const load = useCallback(
    (signal: AbortSignal) => api.ledger(agentId!, signal),
    [api, agentId, refresh],
  );
  const { data, error } = usePolling(agentId ? load : null, 5_000);
  // A selection change must not show the previous agent's ledger while the new request starts.
  const entries = data && data.agent_id === agentId ? data.entries : undefined;
  return (
    <section className="panel ledger-panel" aria-labelledby="ledger-title">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Exposure ledger</p>
          <h2 id="ledger-title">{agentId || "Select an agent"}</h2>
        </div>
        <span className="muted">Refreshes every 5s</span>
      </div>
      <ErrorMessage message={error} />
      {!agentId ? (
        <p className="empty">
          Select an agent above to inspect the credentials it has seen.
        </p>
      ) : !entries ? (
        <p className="empty">Loading exposure ledger…</p>
      ) : !entries.length ? (
        <p className="empty">No credentials recorded for this agent yet.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Credential / type</th>
                <th>Owner</th>
                <th>Source / via</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.secret_ref ?? entry.fingerprint}>
                  <td>
                    <code>{entry.secret_ref || "Unidentified credential"}</code>
                    <span className="cell-note">
                      {entry.secret_type.replaceAll("_", " ")}
                    </span>
                  </td>
                  <td>
                    {entry.owner_agent_id || entry.owner}
                    <span className="cell-note">{entry.owner}</span>
                  </td>
                  <td>
                    {entry.source.replaceAll("_", " ")}
                    <span className="cell-note">{entry.via || "—"}</span>
                  </td>
                  <td>
                    <Status value={entry.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="panel-footnote">
        Credential references only. Raw credential values are never returned.
      </p>
    </section>
  );
}
