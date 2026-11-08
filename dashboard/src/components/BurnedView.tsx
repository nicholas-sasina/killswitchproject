/**
 * Contained agents: what was burned, what it had seen, and how to rebuild it.
 *
 * Burned agents are deliberately absent from the overview roster, which
 * answers "what is running now". They are not thrown away though: a burn is
 * the moment you most need the agent's history, so it gets a page where the
 * ledger and the CloudTrail trail are the main content.
 *
 * Built for a real fleet. A deployed stack accumulates swarm workers,
 * livecheck probes and eval agents, so this can be well over a hundred rows;
 * it filters by role, searches by name or session, and renders a bounded
 * number of rows rather than all of them.
 */
import { useCallback, useMemo, useState } from "react";
import type { BurnApi } from "../api";
import {
  byRole,
  containedFleet,
  copyName,
  joinable,
  roleForAgent,
  type FleetAgent,
} from "../fleet";
import type { LedgerEvent, Role } from "../contracts";
import { usePolling } from "../polling";
import { AddAgent } from "./AddAgent";
import { BlastTimeline } from "./BlastTimeline";
import { Ledger } from "./Ledger";
import { Status, Timestamp } from "./common";

/** Rows drawn before the list is truncated. */
const ROW_CAP = 60;

export function BurnedView({
  api,
  fleet,
  events,
  instances,
  enabled,
}: {
  api: BurnApi;
  fleet: FleetAgent[];
  events: LedgerEvent[];
  instances: import("../contracts").Instance[];
  enabled: boolean;
}) {
  const contained = useMemo(
    () => containedFleet(fleet, events),
    [fleet, events],
  );
  const roles = useMemo(
    () => byRole(contained.map((item) => item.entry)),
    [contained],
  );
  const [roleFilter, setRoleFilter] = useState("");
  const [query, setQuery] = useState("");
  const [selectedKey, setSelectedKey] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [rebuild, setRebuild] = useState<{
    role?: Role;
    template?: Role;
    suggestedName?: string;
  }>();

  const search = query.trim().toLowerCase();
  const matching = useMemo(
    () =>
      contained.filter(
        (item) =>
          (!roleFilter || (item.entry.role || "no role") === roleFilter) &&
          (!search ||
            item.entry.agentId.toLowerCase().includes(search) ||
            (item.entry.sessionId ?? "").toLowerCase().includes(search)),
      ),
    [contained, roleFilter, search],
  );
  const shown = showAll ? matching : matching.slice(0, ROW_CAP);
  const selected =
    matching.find((item) => item.entry.key === selectedKey) ?? matching[0];

  // Only needed once something is selected, so it costs nothing on an empty
  // page.
  const loadRoles = useCallback(
    (signal: AbortSignal) => api.roles(signal),
    [api],
  );
  const rolePoll = usePolling(enabled && selected ? loadRoles : null, 30_000);
  const roleList = rolePoll.data?.roles ?? [];
  const selectedRole = selected
    ? roleList.find(
        (role) =>
          role.name ===
          roleForAgent(
            selected.entry,
            roleList.map((item) => item.name),
          ),
      )
    : undefined;

  if (!contained.length)
    return (
      <section className="panel">
        <div className="panel-heading">
          <h2>Nothing contained</h2>
        </div>
        <div className="empty">
          <p>
            No agent has been burned. When you burn one it moves here, with the
            credentials it had seen and every AWS call made with its keys.
          </p>
        </div>
      </section>
    );

  return (
    <>
      <div className="burned-layout">
        <section className="panel" aria-labelledby="burned-title">
          <div className="panel-heading">
            <h2 id="burned-title">
              {matching.length} contained{" "}
              {matching.length === 1 ? "agent" : "agents"}
            </h2>
            <span className="meta">Most recent first</span>
          </div>

          {(roles.length > 1 || contained.length > 10) && (
            <div className="alert-filters">
              <label>
                Role
                <select
                  value={roleFilter}
                  onChange={(event) => setRoleFilter(event.target.value)}
                >
                  <option value="">All roles ({contained.length})</option>
                  {roles.map(([role, count]) => (
                    <option key={role} value={role}>
                      {role} ({count})
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Find
                <input
                  type="search"
                  value={query}
                  placeholder="Agent or session"
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
            </div>
          )}

          {!matching.length ? (
            <p className="empty">No contained agent matches these filters.</p>
          ) : (
            <>
              <ul className="burned-list">
                {shown.map((item) => (
                  <li key={item.entry.key}>
                    <button
                      type="button"
                      className="burned-row"
                      aria-pressed={item.entry.key === selected?.entry.key}
                      onClick={() => setSelectedKey(item.entry.key)}
                    >
                      <span className="burned-head">
                        <span className="burned-id">{item.entry.agentId}</span>
                        <Status value="revoked" />
                      </span>
                      <span className="burned-meta">
                        {item.entry.role && (
                          <>
                            {item.entry.role}
                            <span className="sep">·</span>
                          </>
                        )}
                        {item.entry.exposures ?? 0} credential
                        {item.entry.exposures === 1 ? "" : "s"} seen
                        {item.revokedAt && (
                          <>
                            <span className="sep">·</span>
                            burned <Timestamp value={item.revokedAt} />
                          </>
                        )}
                      </span>
                      {item.entry.sessionId && (
                        <span className="burned-session">
                          <code>{item.entry.sessionId}</code>
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
              {matching.length > ROW_CAP && (
                <div className="trail-more">
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => setShowAll((current) => !current)}
                  >
                    {showAll
                      ? `Show first ${ROW_CAP}`
                      : `Show all ${matching.length}`}
                  </button>
                </div>
              )}
            </>
          )}
        </section>

        <div className="burned-detail">
          <section className="panel">
            <div className="panel-heading">
              <h2>{selected?.entry.agentId}</h2>
              <span className="meta">
                {selected?.observed
                  ? "Burned during this session"
                  : "Burned before this dashboard opened"}
              </span>
            </div>
            <p className="panel-intro">
              Its badge no longer authenticates and the credentials it owned
              have been rotated. Credentials it only saw belong to someone else
              and are flagged rather than rotated.
            </p>
            {selected?.note && <p className="panel-intro">{selected.note}</p>}
            <div className="rebuild-actions">
              {selectedRole ? (
                <>
                  {joinable(selectedRole) && (
                    <button
                      type="button"
                      className="secondary-button"
                      disabled={!enabled}
                      onClick={() => setRebuild({ role: selectedRole })}
                    >
                      Re-add with the same setup
                    </button>
                  )}
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={!enabled}
                    onClick={() =>
                      setRebuild({
                        template: selectedRole,
                        suggestedName: copyName(
                          selectedRole.name,
                          roleList.map((role) => role.name),
                        ),
                      })
                    }
                  >
                    Copy setup to a new role
                  </button>
                </>
              ) : (
                <p className="muted">
                  {rolePoll.data
                    ? "No matching role, so this agent cannot be rebuilt from here. Add an agent from the overview instead."
                    : "Loading roles…"}
                </p>
              )}
            </div>
            {selectedRole && joinable(selectedRole) && (
              <p className="panel-footnote">
                Re-adding issues a fresh join token for{" "}
                <code>{selectedRole.name}</code>. The new instance gets the same
                tools and permissions and its own newly minted credentials;
                nothing the burned agent held is reused.
              </p>
            )}
            {selectedRole && !joinable(selectedRole) && (
              <p className="panel-footnote">
                <code>{selectedRole.name}</code> belongs to the demo scenario,
                which starts its agent itself, so the API would refuse a join
                token for it. To run the same setup again, copy it to a new
                role: the copy gets its own newly minted credentials.
              </p>
            )}
          </section>

          <Ledger api={api} agentId={selected?.entry.agentId} refresh={0} />
          <BlastTimeline api={api} agentId={selected?.entry.agentId} />
        </div>
      </div>

      {rebuild && (
        <AddAgent
          api={api}
          roles={roleList}
          instances={instances}
          role={rebuild.role}
          template={rebuild.template}
          suggestedName={rebuild.suggestedName}
          onCreated={() => {}}
          onClose={() => setRebuild(undefined)}
        />
      )}
    </>
  );
}
