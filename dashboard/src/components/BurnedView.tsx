/**
 * Contained agents: what was burned, what it had seen, and how to rebuild it.
 *
 * Burned agents are deliberately absent from the overview, which answers
 * "what is running now". Keeping dead agents in that list made the fleet look
 * bigger than it was and buried the live ones. They are not thrown away
 * though: a burn is the moment you most need the agent's history, so it gets
 * a page where the ledger and the CloudTrail trail are the main content.
 */
import { useCallback, useState } from "react";
import type { BurnApi } from "../api";
import { burnedAgents, roleForAgent } from "../burned";
import type { AgentState, LedgerEvent, Role } from "../contracts";
import { usePolling } from "../polling";
import { AddAgent } from "./AddAgent";
import { BlastTimeline } from "./BlastTimeline";
import { Ledger } from "./Ledger";
import { Status, Timestamp } from "./common";

export function BurnedView({
  api,
  agents,
  events,
  enabled,
}: {
  api: BurnApi;
  agents: AgentState[];
  events: LedgerEvent[];
  enabled: boolean;
}) {
  const burned = burnedAgents(agents, events);
  const [selectedId, setSelectedId] = useState("");
  // Rebuilding needs the role list: either to mint another token for the
  // agent's own role, or to copy its capabilities into a new one.
  const loadRoles = useCallback(
    (signal: AbortSignal) => api.roles(signal),
    [api],
  );
  const loadInstances = useCallback(
    (signal: AbortSignal) => api.instances(undefined, signal),
    [api],
  );
  const rolePoll = usePolling(
    enabled && burned.length ? loadRoles : null,
    10_000,
  );
  const instancePoll = usePolling(
    enabled && burned.length ? loadInstances : null,
    10_000,
  );
  const roles = rolePoll.data?.roles ?? [];
  const instances = instancePoll.data?.instances ?? [];
  const [rebuild, setRebuild] = useState<{
    role?: Role;
    template?: Role;
    suggestedName?: string;
  }>();

  const selected =
    burned.find((entry) => entry.agent.agent_id === selectedId) ?? burned[0];
  const selectedRole = selected
    ? roles.find(
        (role) =>
          role.name ===
          roleForAgent(
            selected.agent,
            roles.map((r) => r.name),
          ),
      )
    : undefined;

  if (!burned.length)
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
              {burned.length} contained{" "}
              {burned.length === 1 ? "agent" : "agents"}
            </h2>
            <span className="meta">Most recent first</span>
          </div>
          <ul className="burned-list">
            {burned.map((entry) => (
              <li key={entry.agent.agent_id}>
                <button
                  type="button"
                  className="burned-row"
                  aria-pressed={
                    entry.agent.agent_id === selected?.agent.agent_id
                  }
                  onClick={() => setSelectedId(entry.agent.agent_id)}
                >
                  <span className="burned-head">
                    <span className="burned-id">{entry.agent.agent_id}</span>
                    <Status value="revoked" />
                  </span>
                  <span className="burned-meta">
                    {entry.agent.role && (
                      <>
                        {entry.agent.role}
                        <span className="sep">·</span>
                      </>
                    )}
                    {entry.agent.exposures ?? 0} credential
                    {entry.agent.exposures === 1 ? "" : "s"} seen
                    {entry.revokedAt && (
                      <>
                        <span className="sep">·</span>
                        burned <Timestamp value={entry.revokedAt} />
                      </>
                    )}
                  </span>
                  {entry.note && (
                    <span className="burned-note">{entry.note}</span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </section>

        <div className="burned-detail">
          <section className="panel">
            <div className="panel-heading">
              <h2>{selected?.agent.agent_id}</h2>
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
            <div className="rebuild-actions">
              {selectedRole ? (
                <>
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={!enabled}
                    onClick={() => setRebuild({ role: selectedRole })}
                  >
                    Re-add with the same setup
                  </button>
                  <button
                    type="button"
                    className="secondary-button"
                    disabled={!enabled}
                    onClick={() =>
                      setRebuild({
                        template: selectedRole,
                        suggestedName: `${selectedRole.name}-2`,
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
            {selectedRole && (
              <p className="panel-footnote">
                Re-adding issues a fresh join token for{" "}
                <code>{selectedRole.name}</code>. The new instance gets the same
                tools and permissions and its own newly minted credentials;
                nothing the burned agent held is reused.
              </p>
            )}
          </section>

          <Ledger api={api} agentId={selected?.agent.agent_id} refresh={0} />
          <BlastTimeline api={api} agentId={selected?.agent.agent_id} />
        </div>
      </div>

      {rebuild && (
        <AddAgent
          api={api}
          roles={roles}
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
