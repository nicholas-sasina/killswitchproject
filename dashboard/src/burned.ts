/**
 * Which agents have been contained, and what we can say about each.
 *
 * Derived from the agent list and the event stream the dashboard already
 * polls, so this costs no extra requests.
 *
 * Deliberately conservative about counts. The event stream only holds what
 * has arrived since the dashboard was opened, so counting "rotated" events
 * here would under-report after a reload. The authoritative record of what
 * happened to an agent's credentials is its exposure ledger, which the view
 * loads for the selected agent instead.
 */
import type { AgentState, LedgerEvent } from "./contracts";

export interface BurnedAgent {
  agent: AgentState;
  /** When the badge was revoked, if that event is in the current stream. */
  revokedAt?: string;
  /** The burn's own note, if the event carried one. */
  note?: string;
  /** True when the revocation was observed rather than inferred from status. */
  observed: boolean;
}

/**
 * Contained agents, most recently revoked first. Agents whose revocation
 * predates this session have no timestamp, so they sort after the ones we
 * watched happen.
 */
export function burnedAgents(
  agents: AgentState[],
  events: LedgerEvent[],
): BurnedAgent[] {
  const revocation = new Map<string, LedgerEvent>();
  for (const event of events) {
    if (event.action !== "revoked") continue;
    const seen = revocation.get(event.agent_id);
    // Event ids sort by time, so the largest is the most recent revocation.
    if (!seen || event.event_id > seen.event_id)
      revocation.set(event.agent_id, event);
  }
  return agents
    .filter((agent) => agent.status === "revoked")
    .map((agent) => {
      const event = revocation.get(agent.agent_id);
      return {
        agent,
        revokedAt: event?.ts,
        note: event?.detail ?? undefined,
        observed: Boolean(event),
      };
    })
    .sort((a, b) => (b.revokedAt ?? "").localeCompare(a.revokedAt ?? ""));
}

/**
 * The role name a burned agent can be re-added through.
 *
 * Scenario agents are a role with one instance, named after the agent, so a
 * burned scenario agent maps to its own role. `role` on the agent state is
 * the display name; `roles` is the authoritative list to match against.
 */
export function roleForAgent(
  agent: AgentState,
  roleNames: string[],
): string | undefined {
  if (agent.role && roleNames.includes(agent.role)) return agent.role;
  // Scenario roles are named for the agent they belong to.
  const byAgent = roleNames.find((name) => name === agent.agent_id);
  if (byAgent) return byAgent;
  // Fall back to a role whose name is a suffix of the agent id, which is how
  // scenario agents (agent-reader) relate to their role (reader).
  return roleNames.find((name) => agent.agent_id.endsWith(`-${name}`));
}
