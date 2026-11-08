/**
 * One burned agent as an incident row: why it was burned, what Burn did,
 * and where it stands. Everything here is read from the API's own events;
 * when an event is not in the loaded history, the row says less rather than
 * guessing.
 *
 * The API writes a burn's cause into its `revoked` event:
 *   a person    actor = their email ("admin" in local development),
 *               detail "N session(s) revoked (<reason>)"
 *   a canary    secret_ref = the canary, detail "canary incident evt_…";
 *               the `canary_used` event before it ends "(from <ip>)"
 *   a source    POST /burn/by-source's default reason, "read <tool>:<resource>",
 *               on every agent that read it
 * and what it did as `rotated` and `flagged` events on the burned agent's id
 * ("rotation failed: …" or "rotation deferred: …" when it could not rotate).
 */
import type { Flag, LedgerEvent } from "../../contracts";
import {
  evidenceAgent,
  evidenceSentence,
  firstEvidence,
  isShared,
} from "../../features/flags/describe";
import {
  burnReason,
  dayTime,
  findAgent,
  isCanaryRef,
  isSameDay,
  keyName,
  personLabel,
  shortTime,
  type ConsoleAgent,
  type ConsoleModel,
  type Incident,
} from "../../model";
import { plural } from "../../plural";

export type CauseKind =
  /** Its canary was used from outside Burn; Burn burned it by itself. */
  | "canary"
  /** A person pressed Burn. */
  | "person"
  /** Burned with everyone who read one source. */
  | "source"
  /** The burn is recorded, but not who or what started it. */
  | "unattributed"
  /** The burn's event is older than the loaded history. */
  | "unrecorded";

export interface BurnCause {
  kind: CauseKind;
  /** Who pressed Burn, as the console writes people. */
  actor?: string;
  reason?: string;
  /** For a source burn: what was read, "<tool>:<resource>". */
  sourceRef?: string;
  /** For a source burn: how many other agents the same burn took. */
  others?: number;
  /** For a canary: where it was used from, when the API recorded it. */
  ip?: string;
}

const BURN_ACTIONS: ReadonlySet<string> = new Set([
  "revoked",
  "canary_used",
  "rotated",
  "flagged",
]);

/** The few events a burn writes, out of a stream of thousands. */
export function burnEvents(events: LedgerEvent[]): LedgerEvent[] {
  return events.filter((event) => BURN_ACTIONS.has(event.action));
}

function about(agent: ConsoleAgent, event: LedgerEvent): boolean {
  if (event.session_id)
    return Boolean(agent.sessionId) && event.session_id === agent.sessionId;
  return event.agent_id === agent.agentId;
}

/** The `revoked` event that burned this agent, newest first. */
export function revokedEventFor(
  agent: ConsoleAgent,
  events: LedgerEvent[],
): LedgerEvent | undefined {
  return events.find(
    (event) => event.action === "revoked" && about(agent, event),
  );
}

/** "read read_ticket:T-1001", as POST /burn/by-source writes its reason. */
const SOURCE_REASON = /^read ([A-Za-z0-9_.-]{1,64}:.+)$/s;

/** What started this agent's burn. `events` are newest first. */
export function burnCause(
  agent: ConsoleAgent,
  events: LedgerEvent[],
): BurnCause {
  const revoked = revokedEventFor(agent, events);
  if (!revoked) return { kind: "unrecorded" };
  const detail = revoked.detail ?? "";
  if (isCanaryRef(revoked.secret_ref) || /^canary incident/i.test(detail)) {
    const used = events.find(
      (event) =>
        event.action === "canary_used" &&
        about(agent, event) &&
        event.ts <= revoked.ts,
    );
    const ip = used?.detail?.match(/from ([0-9a-fA-F.:]+)\)?\s*$/)?.[1];
    return { kind: "canary", ...(ip ? { ip } : {}) };
  }
  const reason = burnReason(detail) || undefined;
  const actor =
    revoked.actor && revoked.actor !== "system"
      ? personLabel(revoked.actor)
      : undefined;
  const source = reason?.match(SOURCE_REASON)?.[1];
  if (source) {
    const at = Date.parse(revoked.ts);
    const together = new Set<string>();
    for (const event of events) {
      if (event.action !== "revoked" || event.agent_id === revoked.agent_id)
        continue;
      if (burnReason(event.detail) !== reason) continue;
      if ((event.actor ?? "") !== (revoked.actor ?? "")) continue;
      if (Math.abs(Date.parse(event.ts) - at) > 60_000) continue;
      together.add(event.agent_id);
    }
    return {
      kind: "source",
      sourceRef: source,
      others: together.size,
      ...(actor ? { actor } : {}),
    };
  }
  if (actor) return { kind: "person", actor, ...(reason ? { reason } : {}) };
  return { kind: "unattributed", ...(reason ? { reason } : {}) };
}

/** The "Why it was burned" column. */
export function causeSentence(cause: BurnCause): string {
  switch (cause.kind) {
    case "canary":
      return `Its canary was used${cause.ip ? ` from ${cause.ip}` : ""}, outside Burn. Burned automatically`;
    case "person":
      return `Burned by ${cause.actor}${cause.reason ? `: “${cause.reason}”` : ""}`;
    case "source": {
      const others = cause.others ?? 0;
      return others
        ? `Read ${cause.sourceRef}; burned with the ${others === 1 ? "1 other" : `${others} others`} that read it`
        : `Read ${cause.sourceRef}; burned as the only agent that read it`;
    }
    case "unattributed":
      return cause.reason
        ? `Burned: “${cause.reason}”`
        : "Burned. No reason was recorded";
    case "unrecorded":
      return "Burned before the events loaded here, so the reason isn't shown";
  }
}

/**
 * Whether the burn's rotation failed or was deferred and nothing has rotated
 * since. Read from the detail alone: Burn's canary path writes these
 * without naming a key.
 */
function rotationProblem(
  agent: ConsoleAgent,
  events: LedgerEvent[],
): "failed" | "deferred" | undefined {
  const after = agent.burnedAtFrom === "event" ? agent.burnedAt : undefined;
  let problem: "failed" | "deferred" | undefined;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.agent_id !== agent.agentId || event.source !== "burn") continue;
    if (after && event.ts < after) continue;
    const found = event.detail?.match(/^rotation (failed|deferred)/)?.[1];
    if (event.action === "flagged" && found)
      problem = found as "failed" | "deferred";
    else if (event.action === "rotated") problem = undefined;
  }
  return problem;
}

/** The "What Burn did" column, from the burn's own events and the instance. */
export function outcomeSentence(
  incident: Incident,
  events: LedgerEvent[],
  model: ConsoleModel,
): string {
  const { agent } = incident;
  const parts = ["Badge revoked"];
  if (incident.rotated.length)
    parts.push(`${plural(incident.rotated.length, "owned key")} rotated`);
  const problem = rotationProblem(agent, events);
  if (problem === "failed") parts.push("rotation failed: finish it by hand");
  if (problem === "deferred")
    parts.push("rotation deferred while other sessions use its keys");
  if (incident.flagged.length === 1)
    parts.push(`${keyName(incident.flagged[0])} sent to its owner`);
  else if (incident.flagged.length > 1)
    parts.push(`${incident.flagged.length} shared keys sent to their owners`);
  else if (revokedEventFor(agent, events)) parts.push("no shared key to flag");
  const instance = agent.instance;
  if (instance?.replaced_by)
    parts.push(
      `replaced by ${findAgent(model, instance.replaced_by)?.name ?? "a fresh agent"}`,
    );
  else if (instance?.replacement?.status === "pending")
    parts.push("replacement pending");
  else if (instance?.replacement?.status === "needs_person")
    parts.push("replacement needs a person");
  return parts.join(", ");
}

/** The status pill: what is still open, or Burned once nothing is. */
export function statusLabel(incident: Incident): {
  text: string;
  tone: "flag" | "neutral";
} {
  switch (incident.status) {
    case "keys-open":
      return {
        text:
          incident.openKeys.length === 1
            ? "Key open"
            : `${incident.openKeys.length} keys open`,
        tone: "flag",
      };
    case "needs-person":
      return { text: "Needs a person", tone: "flag" };
    case "rotation-failed":
      return { text: "Rotation failed", tone: "flag" };
    case "burned":
      return { text: "Burned", tone: "neutral" };
  }
}

/** Under the name: "worker, today 7:42 PM", or "reader, Nov 6, 3:15 PM". */
export function burnWhen(agent: ConsoleAgent, now: Date): string {
  const at = agent.burnedAt;
  if (!at) return agent.role;
  const when =
    agent.burnedAtFrom === "last-seen"
      ? `last seen ${dayTime(at, now)}`
      : isSameDay(at, now)
        ? `today ${shortTime(at)}`
        : dayTime(at, now);
  return `${agent.role}, ${when}`;
}

export interface CauseCounts {
  canary: number;
  person: number;
  source: number;
  /** Burns whose trigger the loaded events don't say. */
  unknown: number;
}

/** "How burns started": one count per burned agent, so they sum to the rows. */
export function causeCounts(causes: BurnCause[]): CauseCounts {
  const counts: CauseCounts = { canary: 0, person: 0, source: 0, unknown: 0 };
  for (const cause of causes) {
    if (cause.kind === "canary") counts.canary += 1;
    else if (cause.kind === "person") counts.person += 1;
    else if (cause.kind === "source") counts.source += 1;
    else counts.unknown += 1;
  }
  return counts;
}

/** "worker-07", "worker-07 and worker-12", "worker-07, worker-12 and 2 more". */
export function nameList(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names[0]}, ${names[1]} and ${names.length - 2} more`;
}

/** The hero's two lines: "1 agent burned today." "1 key waits on its owner." */
export function burnedTodayLine(count: number): string {
  if (!count) return "No agent burned today.";
  return `${plural(count, "agent")} burned today.`;
}

export function keysWaitingLine(count: number): string {
  if (!count) return "No key waits on its owner.";
  return count === 1
    ? "1 key waits on its owner."
    : `${count} keys wait on their owners.`;
}

/**
 * Under a key in "Still open": which incident it came from, whose it is,
 * and which running agents also saw it (from the loaded events).
 */
export function stillOpenBody(
  flag: Flag,
  incidents: Incident[],
  seen: ConsoleAgent[] | undefined,
  model: ConsoleModel,
): string {
  const sentences: string[] = [];
  const from = incidents
    .filter((incident) => incident.flagged.includes(flag.secret_ref))
    .map((incident) => incident.agent);
  if (from.length)
    sentences.push(
      `From the ${nameList(from.map((agent) => agent.name))} incident${from.length > 1 ? "s" : ""}.`,
    );
  else {
    const first = firstEvidence(flag);
    if (first)
      sentences.push(
        `${evidenceSentence(first.evidence, evidenceAgent(model, first.evidence)?.name)}.`,
      );
  }
  if (isShared(flag))
    sentences.push("A shared key: its owner decides whether to rotate it.");
  else
    sentences.push(
      `It belongs to ${findAgent(model, flag.owner_agent_id)?.name ?? flag.owner_agent_id}. Its owner decides whether to rotate it.`,
    );
  const running = (seen ?? []).filter(
    (agent) =>
      agent.state === "running" && !from.some((item) => item.key === agent.key),
  );
  if (running.length === 1)
    sentences.push(`${running[0].name}, still running, also saw it.`);
  else if (running.length > 1)
    sentences.push(
      `${running[0].name} and ${running.length - 1} more running agents also saw it.`,
    );
  return sentences.join(" ");
}
