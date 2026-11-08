/**
 * One fleet list, built from both places the API describes agents.
 *
 * `/events` describes only the scenario agents. `/instances` returns every
 * instance that ever joined, swarm workers and eval harness agents included,
 * so the fleet is built from both or a burned swarm worker would never
 * appear.
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
 * How far a session has gone. A session only moves forward (it never becomes
 * active again once ended), so when two reports disagree the furthest one is
 * the true one.
 */
const STAGE: Record<FleetStatus, number> = {
  idle: 0,
  active: 1,
  exited: 2,
  revoked: 3,
};

/**
 * Every agent the API knows about, de-duplicated by session.
 *
 * `/instances` supplies the entry where both describe the same session,
 * because it carries the role, owner and timestamps. Its status can lag,
 * though: the event feed is polled every second and `/instances` every few,
 * and the latter takes seconds to answer for a large fleet. So the status is
 * whichever report is further along, and a burn shows as soon as the feed
 * has it. A scenario agent with no matching instance is still included, so
 * the fleet is complete against an API that has no instance records.
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
    const known = byKey.get(entry.key);
    if (!known) byKey.set(entry.key, entry);
    else if (STAGE[entry.status] > STAGE[known.status])
      byKey.set(entry.key, { ...known, status: entry.status });
  }
  return [...byKey.values()];
}

/**
 * Mark the sessions a burn from this dashboard just revoked, ahead of the
 * next poll that would report it.
 */
export function markRevoked(
  fleet: FleetAgent[],
  sessions: ReadonlySet<string>,
): FleetAgent[] {
  if (!sessions.size) return fleet;
  return fleet.map((entry) =>
    entry.sessionId &&
    sessions.has(entry.sessionId) &&
    entry.status !== "revoked"
      ? { ...entry, status: "revoked" }
      : entry,
  );
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
  /** When the burn revoked this session, if its event is in the stream loaded. */
  revokedAt?: string;
  /** The burn's own note, if the event carried one. */
  note?: string;
}

/**
 * Burned agents, most recent first.
 *
 * Only `revoked` counts as contained. An `exited` instance finished its work
 * and was never burned, so including it would overstate what containment did.
 *
 * A burn's `revoked` event names the session it revoked, so events are
 * matched by session: one agent name can have several sessions, and each
 * keeps its own burn time rather than borrowing the latest one. A session
 * with no matching event in the stream loaded shows no time.
 */
export function containedFleet(
  fleet: FleetAgent[],
  events: LedgerEvent[],
): ContainedAgent[] {
  const bySession = new Map<string, LedgerEvent>();
  const byAgent = new Map<string, LedgerEvent>(); // entries without a session
  for (const event of events) {
    if (event.action !== "revoked") continue;
    const [map, key] = event.session_id
      ? [bySession, event.session_id]
      : [byAgent, event.agent_id];
    const seen = map.get(key);
    // Event ids sort by time, so the largest is the most recent revocation.
    if (!seen || event.event_id > seen.event_id) map.set(key, event);
  }
  return fleet
    .filter((entry) => entry.status === "revoked")
    .map((entry) => {
      const event = entry.sessionId
        ? bySession.get(entry.sessionId)
        : byAgent.get(entry.agentId);
      return {
        entry,
        revokedAt: event?.ts,
        note: event?.detail ?? undefined,
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
 * `agent-<role>` convention that scenario agents follow. The longest name
 * wins, so `agent-code-reader` is code-reader's, not reader's.
 */
export function roleForAgent(
  agent: { agentId: string; role?: string | null },
  roleNames: string[],
): string | undefined {
  if (agent.role && roleNames.includes(agent.role)) return agent.role;
  const byName = roleNames.find((name) => name === agent.agentId);
  if (byName) return byName;
  return roleNames
    .filter((name) => agent.agentId.endsWith(`-${name}`))
    .sort((a, b) => b.length - a.length)[0];
}

/** Role names: a lowercase letter or digit, then up to 31 more (contracts/burn.schema.json). */
const ROLE_NAME_MAX = 32;

/**
 * A free name for a copy of a role: `<name>-2`, `-3`, and so on, shortened so
 * it stays within the 32 characters a role name may have.
 */
export function copyName(name: string, taken: string[]): string {
  for (let n = 2; ; n += 1) {
    const suffix = `-${n}`;
    const base = name
      .slice(0, ROLE_NAME_MAX - suffix.length)
      .replace(/-+$/, "");
    const candidate = `${base}${suffix}`;
    if (!taken.includes(candidate)) return candidate;
  }
}

/**
 * Whether a join token can start a new instance of this role. A scenario
 * role's agent is started by its scenario, and the API refuses a join for one
 * (403), so a token minted for it could never be used.
 */
export function joinable(role: { managed_by: string }): boolean {
  return role.managed_by !== "scenario";
}
