/**
 * Agents by name. The API knows an agent by `agent_id` and its session; the
 * console calls it `<role>-NN`, numbered by when it started within its role,
 * oldest first. A replacement starts after the agent it replaces, so it
 * takes the next number.
 */

export interface Nameable {
  /** The session, or the agent id where there is no session. */
  key: string;
  agentId: string;
  role?: string | null;
  createdAt?: string | null;
}

/**
 * The role an agent belongs to. Scenario agents may arrive with no role from
 * /events; their agent id follows the `agent-<role>` convention.
 */
export function roleOf(agent: {
  agentId: string;
  role?: string | null;
}): string {
  return agent.role || agent.agentId.replace(/^agent-/, "") || agent.agentId;
}

export function displayName(role: string, number: number): string {
  return `${role}-${String(number).padStart(2, "0")}`;
}

function startedAt(agent: Nameable): number {
  const time = agent.createdAt ? Date.parse(agent.createdAt) : Number.NaN;
  return Number.isNaN(time) ? Number.POSITIVE_INFINITY : time;
}

/**
 * Each agent's number within its role, keyed by `key`: 1 for the oldest.
 * Agents with no start time come last; ties go by key, so the numbering is
 * the same on every page and every poll.
 */
export function numberAgents(agents: Nameable[]): Map<string, number> {
  const byRole = new Map<string, Nameable[]>();
  for (const agent of agents) {
    const role = roleOf(agent);
    const list = byRole.get(role);
    if (list) list.push(agent);
    else byRole.set(role, [agent]);
  }
  const numbers = new Map<string, number>();
  for (const list of byRole.values()) {
    list
      .slice()
      .sort(
        (a, b) =>
          startedAt(a) - startedAt(b) ||
          (a.key < b.key ? -1 : a.key > b.key ? 1 : 0),
      )
      .forEach((agent, index) => numbers.set(agent.key, index + 1));
  }
  return numbers;
}

/** Every agent's display name, keyed by `key`. */
export function displayNames(agents: Nameable[]): Map<string, string> {
  const numbers = numberAgents(agents);
  return new Map(
    agents.map((agent) => [
      agent.key,
      displayName(roleOf(agent), numbers.get(agent.key) ?? 0),
    ]),
  );
}
