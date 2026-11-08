/**
 * One fleet list, built from both places the API describes agents.
 *
 * `/events` returns only the scenario agents (three, in the support-desk
 * demo). `/instances` returns every instance that ever joined, including
 * swarm workers, livecheck probes and eval harness agents. Showing only the
 * first made the dashboard report three agents when a hundred and fifty had
 * run, and made a burned swarm worker invisible.
 *
 * The unit here is the instance, not the agent name: one name can have many
 * sessions, so `agent-reader` legitimately appears eight times with eight
 * different sessions and eight different outcomes. Keying on the session is
 * what keeps those distinct.
 */
import type { AgentState, Instance, LedgerEvent } from "./contracts";

export type FleetStatus = "active" | "revoked" | "idle" | "exited";

export interface FleetAgent {
  /** Stable identity: the session when there is one, else the agent name. */
  key: string;
  agentId: string;
  role?: string | null;
  status: FleetStatus;
  sessionId?: string | null;
  exposures?: number;
  owner?: string | null;
  createdAt?: string;
  lastSeen?: string | null;
  /** Where this entry came from, for the few places it matters. */
  from: "scenario" | "instance";
}

function fromInstance(instance: Instance): FleetAgent {
  return {
    key: instance.session_id,
    agentId: instance.agent_id,
    role: instance.role,
    status: instance.status,
    sessionId: instance.session_id,
    exposures: instance.exposures,
    owner: instance.owner ?? null,
    createdAt: instance.created_at,
    lastSeen: instance.last_seen ?? instance.exited_at ?? null,
    from: "instance",
  };
}

function fromAgentState(agent: AgentState): FleetAgent {
  return {
    key: agent.session_id || agent.agent_id,
    agentId: agent.agent_id,
    role: agent.role,
    status: agent.status,
    sessionId: agent.session_id ?? null,
    exposures: agent.exposures,
    from: "scenario",
  };
}

/**
 * Every agent the API knows about, de-duplicated by session.
 *
 * `/instances` wins where both describe the same session, because it carries
 * the role, owner and timestamps. A scenario agent with no matching instance
 * is still included, so the demo keeps working against an API that has no
 * instance records.
 */
export function mergeFleet(
  agents: AgentState[],
  instances: Instance[],
): FleetAgent[] {
  const byKey = new Map<string, FleetAgent>();
  for (const instance of instances)
    byKey.set(instance.session_id, fromInstance(instance));
  for (const agent of agents) {
    const entry = fromAgentState(agent);
    if (!byKey.has(entry.key)) byKey.set(entry.key, entry);
  }
  return [...byKey.values()];
}

/** Agents running now. Exited and revoked instances are not running. */
export function liveFleet(fleet: FleetAgent[]): FleetAgent[] {
  return fleet
    .filter((entry) => entry.status === "active" || entry.status === "idle")
    .sort(
      (a, b) =>
        (a.role ?? "").localeCompare(b.role ?? "") ||
        a.agentId.localeCompare(b.agentId),
    );
}

export interface ContainedAgent {
  entry: FleetAgent;
  /** When the badge was revoked, if that event is in the current stream. */
  revokedAt?: string;
  /** The burn's own note, if the event carried one. */
  note?: string;
  observed: boolean;
}

/**
 * Burned agents, most recent first.
 *
 * Only `revoked` counts as contained. An `exited` instance finished its work
 * and was never burned, so including it would overstate what containment did.
 *
 * Revocation events are matched by agent name rather than session, because
 * the burn event does not always carry the session. Where one name has
 * several burned sessions they share the most recent timestamp, which is why
 * the row shows the session too.
 */
export function containedFleet(
  fleet: FleetAgent[],
  events: LedgerEvent[],
): ContainedAgent[] {
  const revocation = new Map<string, LedgerEvent>();
  for (const event of events) {
    if (event.action !== "revoked") continue;
    const seen = revocation.get(event.agent_id);
    // Event ids sort by time, so the largest is the most recent revocation.
    if (!seen || event.event_id > seen.event_id)
      revocation.set(event.agent_id, event);
  }
  return fleet
    .filter((entry) => entry.status === "revoked")
    .map((entry) => {
      const event = revocation.get(entry.agentId);
      return {
        entry,
        revokedAt: event?.ts,
        note: event?.detail ?? undefined,
        observed: Boolean(event),
      };
    })
    .sort(
      (a, b) =>
        (
          b.revokedAt ??
          b.entry.lastSeen ??
          b.entry.createdAt ??
          ""
        ).localeCompare(
          a.revokedAt ?? a.entry.lastSeen ?? a.entry.createdAt ?? "",
        ) || a.entry.agentId.localeCompare(b.entry.agentId),
    );
}

/** Counts per role, for a filter that has to cope with a large fleet. */
export function byRole(
  entries: { role?: string | null }[],
): [string, number][] {
  const counts = new Map<string, number>();
  for (const entry of entries) {
    const role = entry.role || "no role";
    counts.set(role, (counts.get(role) ?? 0) + 1);
  }
  return [...counts.entries()].sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
  );
}

/**
 * The role name an agent can be re-added through: exact match first, then the
 * `agent-<role>` convention that scenario and swarm agents follow.
 */
export function roleForAgent(
  agent: { agentId: string; role?: string | null },
  roleNames: string[],
): string | undefined {
  if (agent.role && roleNames.includes(agent.role)) return agent.role;
  const byName = roleNames.find((name) => name === agent.agentId);
  if (byName) return byName;
  return roleNames.find((name) => agent.agentId.endsWith(`-${name}`));
}
