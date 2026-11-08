/**
 * The console's one model of the fleet. Every page and the navigation read
 * their agents, names and counts from here, so a count on one page always
 * matches the same count anywhere else ("header counts always match what's
 * on screen").
 *
 * Words: a role is a kind of agent; an agent is one running copy of a role
 * (an instance, in the API). An agent is running, burned or finished.
 */
import type { Instance, LedgerEvent, Role } from "../contracts";
import { containedFleet, type FleetAgent } from "../fleet";
import { displayName, numberAgents, roleOf } from "./names";
import { isSameDay } from "./time";

export type AgentState = "running" | "burned" | "finished";

export interface ConsoleAgent {
  /** Stable identity: the session, or the agent id with no session. */
  key: string;
  agentId: string;
  sessionId?: string | null;
  role: string;
  /** `<role>-NN`. */
  name: string;
  number: number;
  state: AgentState;
  createdAt?: string;
  lastSeen?: string | null;
  exposures?: number;
  owner?: string | null;
  createdBy?: string | null;
  tokenId?: string | null;
  /** The parent's key, when its parent is known. */
  parent?: string;
  /** The parent's session, as the API gave it, known or not. */
  parentSession?: string | null;
  /** Keys of the agents it started. */
  children: string[];
  /** When it was burned, and where that time came from. */
  burnedAt?: string;
  burnedAtFrom?: "event" | "replacement" | "last-seen";
  /** The burn's own note (a revoked event's detail), when the stream has it. */
  burnNote?: string;
  burnedToday: boolean;
  from: "scenario" | "instance";
  instance?: Instance;
}

export interface RoleGroup {
  name: string;
  /** The role record, once /roles has answered. */
  role?: Role;
  /** Every agent of the role, in number order. */
  agents: ConsoleAgent[];
  running: ConsoleAgent[];
  burned: ConsoleAgent[];
  burnedToday: ConsoleAgent[];
  finished: ConsoleAgent[];
}

export interface Counts {
  running: number;
  burned: number;
  burnedToday: number;
  finished: number;
  agents: number;
}

export interface ConsoleCounts extends Counts {
  /** Roles with an agent running now. */
  rolesRunning: number;
  /** Roles the Overview shows: something running or burned today. */
  rolesNow: number;
  /** Every role known, from /roles and from agents. */
  roles: number;
}

export interface ConsoleModel {
  /** When the model was built; "today" means this day. */
  now: Date;
  agents: ConsoleAgent[];
  byKey: Map<string, ConsoleAgent>;
  /** By agent id: one id can have several sessions (scenario agents). */
  byAgentId: Map<string, ConsoleAgent[]>;
  /** Every role, largest first (running, then burned today), then by name. */
  roles: RoleGroup[];
  roleByName: Map<string, RoleGroup>;
  counts: ConsoleCounts;
}

/**
 * Only an active session is running. A scenario agent reads idle when it has
 * no active session: it never started one, or its latest one ended (the API
 * reports an exited session as idle), so it counts with the finished.
 */
export function stateOf(status: FleetAgent["status"]): AgentState {
  return status === "revoked"
    ? "burned"
    : status === "active"
      ? "running"
      : "finished";
}

export function countAgents(agents: ConsoleAgent[]): Counts {
  const counts: Counts = {
    running: 0,
    burned: 0,
    burnedToday: 0,
    finished: 0,
    agents: agents.length,
  };
  for (const agent of agents) {
    counts[agent.state] += 1;
    if (agent.burnedToday) counts.burnedToday += 1;
  }
  return counts;
}

/**
 * Build the model. `fleet` is the merged fleet (fleet.ts), `events` the
 * loaded stream, `roles` the role records; `now` decides what "today" is.
 */
export function buildConsole({
  fleet,
  events,
  roles = [],
  now = new Date(),
}: {
  fleet: FleetAgent[];
  events: LedgerEvent[];
  roles?: Role[];
  now?: Date;
}): ConsoleModel {
  const numbers = numberAgents(fleet);
  const burns = new Map(
    containedFleet(fleet, events).map((burn) => [burn.entry.key, burn]),
  );
  const bySession = new Map<string, string>();
  for (const entry of fleet)
    if (entry.sessionId) bySession.set(entry.sessionId, entry.key);

  const agents: ConsoleAgent[] = fleet.map((entry) => {
    const role = roleOf(entry);
    const number = numbers.get(entry.key) ?? 0;
    const state = stateOf(entry.status);
    const instance = entry.instance;
    const burn = burns.get(entry.key);
    let burnedAt: string | undefined;
    let burnedAtFrom: ConsoleAgent["burnedAtFrom"];
    if (state === "burned") {
      if (burn?.revokedAt) [burnedAt, burnedAtFrom] = [burn.revokedAt, "event"];
      else if (instance?.replacement?.cutoff_source === "burn_time")
        [burnedAt, burnedAtFrom] = [instance.replacement.cutoff, "replacement"];
      else if (entry.lastSeen)
        [burnedAt, burnedAtFrom] = [entry.lastSeen, "last-seen"];
    }
    const parentSession = instance?.parent_session ?? null;
    return {
      key: entry.key,
      agentId: entry.agentId,
      sessionId: entry.sessionId,
      role,
      name: displayName(role, number),
      number,
      state,
      createdAt: entry.createdAt,
      lastSeen: entry.lastSeen,
      exposures: entry.exposures,
      owner: entry.owner,
      createdBy: instance?.created_by ?? null,
      tokenId: instance?.token_id ?? null,
      parentSession,
      parent: parentSession ? bySession.get(parentSession) : undefined,
      children: [],
      burnedAt,
      burnedAtFrom,
      burnNote: burn?.note,
      burnedToday: state === "burned" && isSameDay(burnedAt, now),
      from: entry.from,
      instance,
    };
  });
  agents.sort(
    (a, b) =>
      (a.role < b.role ? -1 : a.role > b.role ? 1 : 0) || a.number - b.number,
  );

  const byKey = new Map(agents.map((agent) => [agent.key, agent]));
  const byAgentId = new Map<string, ConsoleAgent[]>();
  for (const agent of agents) {
    if (agent.parent) byKey.get(agent.parent)?.children.push(agent.key);
    const list = byAgentId.get(agent.agentId);
    if (list) list.push(agent);
    else byAgentId.set(agent.agentId, [agent]);
  }

  const groups = new Map<string, RoleGroup>();
  const group = (name: string) => {
    let found = groups.get(name);
    if (!found) {
      found = {
        name,
        agents: [],
        running: [],
        burned: [],
        burnedToday: [],
        finished: [],
      };
      groups.set(name, found);
    }
    return found;
  };
  for (const role of roles) group(role.name).role = role;
  for (const agent of agents) {
    const found = group(agent.role);
    found.agents.push(agent);
    found[agent.state].push(agent);
    if (agent.burnedToday) found.burnedToday.push(agent);
  }
  const ordered = [...groups.values()].sort(
    (a, b) =>
      b.running.length - a.running.length ||
      b.burnedToday.length - a.burnedToday.length ||
      (a.name < b.name ? -1 : a.name > b.name ? 1 : 0),
  );

  const counts = countAgents(agents);
  return {
    now,
    agents,
    byKey,
    byAgentId,
    roles: ordered,
    roleByName: groups,
    counts: {
      ...counts,
      rolesRunning: ordered.filter((role) => role.running.length).length,
      rolesNow: ordered.filter(
        (role) => role.running.length || role.burnedToday.length,
      ).length,
      roles: ordered.length,
    },
  };
}

/** A role's own counts, from the same model as every other count. */
export function roleCounts(model: ConsoleModel, name: string): Counts {
  return countAgents(model.roleByName.get(name)?.agents ?? []);
}

/**
 * The agent a route or an event names: a session first, then an agent id
 * (the newest session of that id).
 */
export function findAgent(
  model: ConsoleModel,
  id: string | null | undefined,
): ConsoleAgent | undefined {
  if (!id) return undefined;
  const byKey = model.byKey.get(id);
  if (byKey) return byKey;
  const sessions = model.byAgentId.get(id);
  return sessions?.reduce((newest, agent) =>
    (agent.createdAt ?? "") > (newest.createdAt ?? "") ? agent : newest,
  );
}

/** The agent an event is about: its session if known, else its agent id. */
export function agentForEvent(
  model: ConsoleModel,
  event: Pick<LedgerEvent, "agent_id" | "session_id">,
): ConsoleAgent | undefined {
  return (
    (event.session_id ? model.byKey.get(event.session_id) : undefined) ??
    findAgent(model, event.agent_id)
  );
}

/** Agents burned today, newest first. */
export function burnedToday(model: ConsoleModel): ConsoleAgent[] {
  return model.agents
    .filter((agent) => agent.burnedToday)
    .sort((a, b) => (b.burnedAt ?? "").localeCompare(a.burnedAt ?? ""));
}
