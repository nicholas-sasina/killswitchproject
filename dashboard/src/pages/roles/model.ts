/**
 * What the role pages work out on the client from the console's model and
 * the API's records. Every function here is pure and tested.
 *
 * Names, states and counts come from the console's one model (src/model);
 * nothing here numbers or counts agents itself.
 */
import type {
  EnrollToken,
  LedgerEntry,
  LedgerEvent,
  Role,
  RoleSpec,
} from "../../contracts";
import type { AgentState, ConsoleAgent, ConsoleModel } from "../../model";
import { ago, dayTime, keyName, personLabel } from "../../model";
import { plural } from "../../plural";

/**
 * A role's agents, as the page names them: "Add workers", "every worker".
 * Role names are lowercase words and hyphens, so English's common rules
 * are enough: "analysis" to "analyses", "registry" to "registries", "-es"
 * after s, x, z, ch or sh, and "-s" otherwise.
 */
export function rolePlural(name: string): string {
  if (!name) return "agents";
  if (/[^aeiou]is$/.test(name)) return `${name.slice(0, -2)}es`;
  if (/[^aeiou]y$/.test(name)) return `${name.slice(0, -1)}ies`;
  return /(s|x|z|ch|sh)$/.test(name) ? `${name}es` : `${name}s`;
}

/** "a worker", "an analyst". */
export function aRole(name: string): string {
  return `${/^[aeiou]/.test(name) ? "an" : "a"} ${name}`;
}

/** "1 worker", "3 workers". */
export function countRole(n: number, name: string): string {
  return `${n} ${n === 1 ? name : rolePlural(name)}`;
}

// ---------------------------------------------------------------- the table

export type Filter = AgentState | "all";

export const FILTERS: readonly Filter[] = [
  "running",
  "burned",
  "finished",
  "all",
];

/** The filter in the address, or Running, the default. */
export function filterOf(value: string | null | undefined): Filter {
  return (FILTERS as readonly string[]).includes(value ?? "")
    ? (value as Filter)
    : "running";
}

export interface AgentRow {
  agent: ConsoleAgent;
  /** How many rows above it are its ancestors: the indent. */
  depth: number;
  /** An agent of another role that one of this role's agents started. */
  guest: boolean;
  /** Its parent, when the console knows it. */
  parent?: ConsoleAgent;
  /** Whether its parent is the row it sits under. */
  underParent: boolean;
}

function byStart(a: ConsoleAgent, b: ConsoleAgent): number {
  const at = (agent: ConsoleAgent) =>
    agent.createdAt ? Date.parse(agent.createdAt) : Number.POSITIVE_INFINITY;
  return (
    at(a) - at(b) ||
    a.number - b.number ||
    (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)
  );
}

/**
 * The rows of a role's agents table under a filter.
 *
 * The role's own agents show when their state matches. Agents of other
 * roles that the role's agents started (a reader under the worker that
 * started it) are listed with them: under their parent while it is shown, and
 * otherwise on their own when their state matches. A child sits indented
 * under its parent only while the parent is a row; otherwise it starts a
 * row of its own, and its second line names the parent and its state.
 *
 * Counts never include the other roles' agents: the tabs and the hero
 * count this role's agents only (`roleCounts`).
 *
 * Malformed lineage (a cycle, a parent the API no longer lists) never
 * hides an agent: each one appears exactly once.
 */
export function roleRows(
  model: ConsoleModel,
  role: string,
  filter: Filter,
): AgentRow[] {
  const own = model.roleByName.get(role)?.agents ?? [];
  const members = new Map<string, ConsoleAgent>();
  for (const agent of own) members.set(agent.key, agent);
  const queue = [...own];
  for (let next = queue.shift(); next; next = queue.shift())
    for (const key of next.children) {
      const child = model.byKey.get(key);
      if (child && !members.has(key)) {
        members.set(key, child);
        queue.push(child);
      }
    }

  const kids = new Map<string, ConsoleAgent[]>();
  const roots: ConsoleAgent[] = [];
  for (const agent of [...members.values()].sort(byStart)) {
    if (agent.parent && agent.parent !== agent.key && members.has(agent.parent))
      kids.set(agent.parent, [...(kids.get(agent.parent) ?? []), agent]);
    else roots.push(agent);
  }

  const matches = (agent: ConsoleAgent) =>
    filter === "all" || agent.state === filter;
  const rows: AgentRow[] = [];
  const seen = new Set<string>();
  const visit = (agent: ConsoleAgent, shownParentDepth?: number) => {
    if (seen.has(agent.key)) return;
    seen.add(agent.key);
    const guest = agent.role !== role;
    const underParent = shownParentDepth !== undefined;
    const shown = matches(agent) || (guest && underParent);
    const depth = underParent ? shownParentDepth + 1 : 0;
    if (shown)
      rows.push({
        agent,
        depth,
        guest,
        parent: agent.parent ? model.byKey.get(agent.parent) : undefined,
        underParent,
      });
    for (const child of kids.get(agent.key) ?? [])
      visit(child, shown ? depth : undefined);
  };
  for (const root of roots) visit(root);
  // A cycle has no root: start from its oldest agent instead.
  for (const agent of [...members.values()].sort(byStart)) visit(agent);
  return rows;
}

/** A token's short form, as the table and the Join tokens card show it. */
export function shortToken(tokenId: string): string {
  return tokenId.slice(0, 8);
}

/**
 * The second line under an agent's name: who started it. A child names
 * its parent (with the parent's state when the parent isn't the row
 * above); a replacement names the agent it replaced; otherwise the person
 * who started it, the scenario, or the join token it used.
 */
export function startedBy(row: AgentRow, model: ConsoleModel): string {
  const { agent, parent, underParent, guest } = row;
  if (agent.parentSession) {
    if (!parent) return "Spawned by an agent Burn no longer lists";
    const by = underParent ? parent.name : `${parent.name} (${parent.state})`;
    return guest
      ? `${aRole(agent.role).replace(/^a/, "A")} spawned by ${by}`
      : `Spawned by ${by}`;
  }
  const replaces = agent.instance?.replaces;
  if (replaces) {
    const old = model.byKey.get(replaces);
    return old ? `Replaces ${old.name}` : "A replacement";
  }
  const person = personLabel(agent.createdBy);
  if (person) return person;
  if (agent.from === "scenario") return "Started by the scenario";
  if (agent.tokenId) return `Joined with token ${shortToken(agent.tokenId)}`;
  return "";
}

/** Event sources that mean an agent made a call (as model/activity.ts). */
const CALL_SOURCES = new Set<LedgerEvent["source"]>([
  "tool_response",
  "gateway",
  "handoff",
  "egress",
]);

/**
 * The newest call each agent made in the loaded events, by session (or
 * agent id with no session). `events` is newest first, so the first one
 * seen per agent is its newest.
 */
export function lastCalls(events: LedgerEvent[]): Map<string, string> {
  const calls = new Map<string, string>();
  for (const event of events) {
    if (!CALL_SOURCES.has(event.source)) continue;
    const key = event.session_id || event.agent_id;
    if (!calls.has(key)) calls.set(key, event.ts);
  }
  return calls;
}

/**
 * When an agent was last heard from: the later of its newest call in the
 * event stream and the API's last_seen (its heartbeat, or when it
 * exited). The API keeps no per-call time, so this is the closest to
 * "last call" it allows.
 */
export function lastCall(
  agent: ConsoleAgent,
  calls: Map<string, string>,
): string | undefined {
  const times = [
    calls.get(agent.sessionId || agent.agentId),
    agent.lastSeen ?? undefined,
  ].filter((time): time is string => Boolean(time && Date.parse(time)));
  return times.sort((a, b) => Date.parse(a) - Date.parse(b)).at(-1);
}

/** "2 s ago" while running; a quiet clock time once it stopped. */
export function lastCallText(
  state: AgentState,
  at: string | undefined,
  now: Date,
): { text: string; quiet: boolean } {
  if (!at) return { text: "Not reported", quiet: true };
  return state === "running"
    ? { text: ago(at, now), quiet: false }
    : { text: dayTime(at, now), quiet: true };
}

export interface KeysSeen {
  /** Keys whose value reached the agent. */
  plain: number;
  /** Keys it only ever got as a reference. */
  referenced: number;
}

/**
 * How an agent saw its keys, from its ledger. An entry without `exposure`
 * counts as plain text, as the API defaults it ("seen").
 */
export function keysSeen(entries: LedgerEntry[]): KeysSeen {
  let referenced = 0;
  for (const entry of entries)
    if (entry.exposure === "referenced") referenced += 1;
  return { plain: entries.length - referenced, referenced };
}

/**
 * "1 in plain text, 3 references", "Nothing yet". Until its ledger loads,
 * the agent's key count from /instances, quietly. `hot` marks plain text,
 * which the table sets in ember.
 */
export function keysText(
  seen: KeysSeen | undefined,
  exposures: number | undefined,
  state: AgentState,
): { text: string; hot: boolean; quiet: boolean } {
  const nothing = state === "running" ? "Nothing yet" : "Nothing";
  if (seen) {
    const parts = [
      seen.plain ? `${seen.plain} in plain text` : "",
      seen.referenced ? plural(seen.referenced, "reference") : "",
    ].filter(Boolean);
    return {
      text: parts.length ? parts.join(", ") : nothing,
      hot: seen.plain > 0,
      quiet: false,
    };
  }
  if (exposures === undefined) return { text: "", hot: false, quiet: true };
  return exposures
    ? { text: plural(exposures, "key"), hot: false, quiet: true }
    : { text: nothing, hot: false, quiet: false };
}

// ------------------------------------------------------- the role's records

/** Where a role is defined: "burn.yaml", "this dashboard", "the scenario". */
export function managedIn(role: Role | undefined): string {
  switch (role?.managed_by) {
    case "code":
      return "burn.yaml";
    case "ui":
      return "this dashboard";
    case "scenario":
      return "the scenario";
    default:
      return "";
  }
}

export interface ProtectionRow {
  label: string;
  note: string;
  value: "On" | "Off" | "Report only";
}

/** The Protections card, with the API's defaults for any field left out. */
export function protections(role: Role | undefined): ProtectionRow[] {
  const name = role?.name || "agent";
  const references = role?.references ?? "replace";
  const canaries = role?.canaries ?? true;
  const autoBurn = role?.auto_burn ?? true;
  const replace = role?.replace_on_burn ?? false;
  return [
    {
      label: "Keys as references",
      note:
        references === "replace"
          ? "Agents see a reference, never the value"
          : references === "report"
            ? "Recorded only; agents see the value"
            : "Agents see the value",
      value:
        references === "replace"
          ? "On"
          : references === "report"
            ? "Report only"
            : "Off",
    },
    {
      label: "Canary in every session",
      note: "A decoy key that proves a leak",
      value: canaries ? "On" : "Off",
    },
    {
      label: "Burn on canary use",
      note: canaries ? "Off means alert and flag only" : "No canary to use",
      value: canaries && autoBurn ? "On" : "Off",
    },
    {
      label: `Replace a burned ${name}`,
      note: "Its finished work stays; the rest is quarantined",
      value: replace ? "On" : "Off",
    },
  ];
}

/**
 * Where a role's agents can reach. Burn's Gateway is never blocked; null
 * means the default allowlist, and an empty list means the Gateway only.
 */
export function reach(egress: string[] | null | undefined): {
  hosts: string[];
  text: string;
} {
  if (egress === null || egress === undefined)
    return { hosts: [], text: "The default allowlist and Burn's Gateway." };
  if (!egress.length)
    return { hosts: [], text: "Only Burn's Gateway. Nothing else." };
  return { hosts: egress, text: ", Burn's Gateway. Nothing else." };
}

/**
 * Shared keys the role's agents were seen holding in the loaded events.
 * The API keeps no list of the shared keys a role may use, so this is
 * the evidence of the ones it does.
 */
export function sharedKeys(
  events: LedgerEvent[],
  agentIds: ReadonlySet<string>,
): string[] {
  const keys = new Set<string>();
  for (const event of events)
    if (
      event.owner === "shared" &&
      event.secret_ref &&
      (event.action === "recorded" || event.action === "inherited") &&
      agentIds.has(event.target_agent_id ?? event.agent_id)
    )
      keys.add(keyName(event.secret_ref));
  return [...keys].sort();
}

// -------------------------------------------------------------- join tokens

export interface TokenRow {
  tokenId: string;
  /** This role's agents that joined with it. */
  joined: number;
  /** When the first of them joined. */
  firstJoin?: string;
  /** The token's record, when this tab made or revoked it. */
  meta?: EnrollToken;
}

/**
 * A role's join tokens. The API has no route that lists them, so the card
 * shows the tokens its agents joined with, plus the ones this tab made or
 * revoked (whose records it has). Newest first.
 */
export function roleTokens(
  agents: ConsoleAgent[],
  known: EnrollToken[],
  role: string,
): TokenRow[] {
  const rows = new Map<string, TokenRow>();
  for (const agent of agents) {
    if (!agent.tokenId || agent.role !== role) continue;
    const row = rows.get(agent.tokenId) ?? {
      tokenId: agent.tokenId,
      joined: 0,
    };
    row.joined += 1;
    if (
      agent.createdAt &&
      (!row.firstJoin ||
        Date.parse(agent.createdAt) < Date.parse(row.firstJoin))
    )
      row.firstJoin = agent.createdAt;
    rows.set(agent.tokenId, row);
  }
  for (const meta of known) {
    if (meta.role !== role) continue;
    const row = rows.get(meta.token_id) ?? {
      tokenId: meta.token_id,
      joined: 0,
    };
    rows.set(meta.token_id, { ...row, meta });
  }
  const newest = (row: TokenRow) =>
    Date.parse(row.meta?.created_at ?? row.firstJoin ?? "") || 0;
  return [...rows.values()].sort(
    (a, b) =>
      newest(b) - newest(a) ||
      (a.tokenId < b.tokenId ? -1 : a.tokenId > b.tokenId ? 1 : 0),
  );
}

/** Whether a token can still let agents join, as far as the console knows. */
export function tokenOpen(row: TokenRow, now: Date): boolean {
  const meta = row.meta;
  if (!meta) return true;
  return (
    meta.status === "active" &&
    Date.parse(meta.expires_at) > now.valueOf() &&
    usesLeft(row) > 0
  );
}

/**
 * Uses left: the API's count when this tab got the record, less the
 * agents that joined with it since, whichever is lower.
 */
function usesLeft(row: TokenRow): number {
  const meta = row.meta;
  if (!meta) return 0;
  return Math.max(0, Math.min(meta.uses_left, meta.uses - row.joined));
}

/**
 * The line under a token: "priya@example.com, 7:10 PM. 6 of 20 uses
 * left, expires 9:10 PM." With no record, what its agents show: "14
 * workers joined with it, first at 7:10 PM."
 */
export function tokenLine(row: TokenRow, role: string, now: Date): string {
  const joined = row.joined
    ? `${countRole(row.joined, role)} joined with it`
    : "";
  const meta = row.meta;
  if (!meta) {
    const first = dayTime(row.firstJoin, now);
    return `${joined}${first ? `, first at ${first}` : ""}.`;
  }
  const made = [personLabel(meta.created_by), dayTime(meta.created_at, now)]
    .filter(Boolean)
    .join(", ");
  const expires = dayTime(meta.expires_at, now);
  const state =
    meta.status === "revoked"
      ? "Revoked"
      : meta.status === "expired" ||
          Date.parse(meta.expires_at) <= now.valueOf()
        ? `Expired${expires ? ` ${expires}` : ""}`
        : usesLeft(row) === 0
          ? `All ${plural(meta.uses, "use")} taken`
          : `${usesLeft(row)} of ${plural(meta.uses, "use")} left${expires ? `, expires ${expires}` : ""}`;
  return [made, state, joined]
    .filter(Boolean)
    .map((part) => `${part}.`)
    .join(" ");
}

// ------------------------------------------------------------ the roles list

export interface ListSquare {
  key: string;
  state: AgentState;
  /** "worker-07, running" */
  label: string;
}

export interface ListRow {
  name: string;
  /** "Full access · Support automation · managed in burn.yaml" */
  line: string;
  /** A square per agent running now and per agent burned today. */
  squares: ListSquare[];
  running: number;
  /** "2 burned today", "3 finished", "No agents yet". */
  note: string;
  /** Whether the note is a burn today. */
  hot: boolean;
}

/**
 * One row per role in the model's order (most running, then most burned
 * today, then name), roles with no agents included. The owner is the
 * role's, or else the one its agents report.
 */
export function rolesList(model: ConsoleModel): ListRow[] {
  return model.roles.map((group) => {
    const owner =
      group.role?.owner ??
      group.agents.find((agent) => agent.owner)?.owner ??
      "";
    const where = managedIn(group.role);
    const access = group.role ? accessLevel(group.role) : "";
    const line = [access, owner, where && `managed in ${where}`]
      .filter(Boolean)
      .join(" · ")
      .replace(/^m/, "M");
    const squares: ListSquare[] = group.agents
      .filter((agent) => agent.state === "running" || agent.burnedToday)
      .map((agent) => ({
        key: agent.key,
        state: agent.state,
        label: `${agent.name}, ${agent.state === "running" ? "running" : "burned today"}`,
      }));
    const burnedToday = group.burnedToday.length;
    const finished = group.finished.length;
    const note = burnedToday
      ? `${burnedToday} burned today`
      : finished
        ? `${finished} finished`
        : group.agents.length
          ? "Nothing burned today"
          : "No agents yet";
    return {
      name: group.name,
      line,
      squares,
      running: group.running.length,
      note,
      hot: burnedToday > 0,
    };
  });
}

// ---------------------------------------------------------------- access

/**
 * Every tool in contracts/mcp-tools.json, by name. A Full access role lists
 * them all, because the gateway allows only the tools a role names. A test
 * fails if this copy drifts from the contract.
 */
export const FULL_ACCESS_TOOLS: readonly string[] = [
  "list_tickets",
  "read_ticket",
  "read_config",
  "query_customers",
  "send_message",
  "read_inbox",
  "notify",
];

/**
 * The body that creates a Full access role, the same one `burn run`
 * creates when it names a role after the command: every tool, its own AWS
 * key (which also reaches its model), no narrowing and the API's default
 * protections.
 */
export function fullAccessRole(name: string): RoleSpec {
  return {
    name,
    tools: [...FULL_ACCESS_TOOLS],
    credentials: ["aws_key"],
    may_message: [],
    may_spawn: [],
    egress: null,
    owner: null,
    canaries: true,
    auto_burn: true,
    replace_on_burn: false,
  };
}

export type Access = "Full access" | "Limited";

/** "Full access" when a role has every tool and no egress list; "Limited" otherwise. */
export function accessLevel(role: Pick<Role, "tools" | "egress">): Access {
  const tools = new Set(role.tools);
  const every =
    tools.size === FULL_ACCESS_TOOLS.length &&
    FULL_ACCESS_TOOLS.every((tool) => tools.has(tool));
  return every && role.egress == null ? "Full access" : "Limited";
}
