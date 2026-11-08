import { useCallback } from "react";
import type { BurnApi } from "../api";
import { usePolling } from "../polling";
import { Collapsible } from "./Collapsible";
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
  // Polls whether open or closed: the collapsed heading reports how many
  // credentials this agent has seen, and that number has to stay true.
  const { data, error } = usePolling(agentId ? load : null, 5_000);
  // A selection change must not show the previous agent's ledger while the
  // new request starts.
  const entries = data && data.agent_id === agentId ? data.entries : undefined;
  const flagged = entries?.filter((entry) => entry.status === "flagged").length;

  const summary = !agentId
    ? "No agent selected"
    : !entries
      ? "Loading"
      : entries.length
        ? `${agentId} · ${entries.length} seen${flagged ? ` · ${flagged} flagged` : ""}`
        : `${agentId} · none seen`;

  return (
    <Collapsible
      className="ledger-panel"
      label="Exposure ledger"
      summary={summary}
    >
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
        <div className="table-scroll ledger-scroll">
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
        Refreshes every five seconds.
      </p>
    </Collapsible>
  );
}
