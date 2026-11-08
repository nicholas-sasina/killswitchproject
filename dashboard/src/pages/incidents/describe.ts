/**
 * One burned agent as an incident row: why it was burned, what Burn did,
 * and where it stands. Everything here is read from the API's own events;
 * when an event is not in the loaded history, the row says less rather than
 * guessing.
 *
 * The API writes a burn's cause into its `revoked` event (model/burns.ts
 * reads it the same way for every page):
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
  burnTrigger,
  dayTime,
  findAgent,
  isSameDay,
  keyName,
  shortTime,
  triggerShort,
  type BurnTrigger,
  type ConsoleAgent,
  type ConsoleModel,
  type Incident,
} from "../../model";
import { plural } from "../../plural";

/**
 * Why an agent was burned. The trigger is the console's shared reading
 * (model/burns.ts), so this list and the Overview word a burn the same way;
 * the rest is what only this list has room for.
 */
export interface BurnCause {
  trigger: BurnTrigger;
  /** For a burn of everyone who read one source: "<tool>:<resource>". */
  source?: string;
  /** For a source burn: how many other agents the same burn took. */
  others?: number;
  /** No event of this burn is in the loaded history. */
  unloaded?: boolean;
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

/** An event of this agent's burn, matched as burnTrigger matches it. */
function about(agent: ConsoleAgent, event: LedgerEvent): boolean {
  return event.session_id
    ? event.session_id === agent.sessionId || event.session_id === agent.key
    : event.agent_id === agent.agentId;
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
  const trigger = burnTrigger(agent, events);
  const revoked = revokedEventFor(agent, events);
  if (trigger.kind === "unknown" && !revoked && !agent.burnNote)
    return { trigger, unloaded: true };
  const reason = "reason" in trigger ? trigger.reason : "";
  const source = reason.match(SOURCE_REASON)?.[1];
  if (!source) return { trigger };
  if (!revoked) return { trigger, source };
  // The same POST /burn/by-source writes one revoked event per agent, all
  // with the same reason and actor, within moments of each other.
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
  return { trigger, source, others: together.size };
}

/**
 * The "Why it was burned" column: the Overview's short line, with the
 * canary's address and a source burn's reach when the events have them.
 */
export function causeSentence(cause: BurnCause): string {
  if (cause.unloaded)
    return "Burned before the loaded events, so the reason isn't shown";
  const { trigger, others } = cause;
  const short = triggerShort(trigger);
  if (trigger.kind === "canary" && trigger.ip)
    return `${short}, from ${trigger.ip}`;
  if (cause.source && others !== undefined)
    return others
      ? `${short}, with the ${others === 1 ? "1 other" : `${others} others`} that read it`
      : `${short}, the only agent that read it`;
  return short;
}

/**
 * Whether the burn's rotation failed or was deferred and nothing has rotated
 * since. Read from the detail alone: Burn's canary path writes these
 * without naming a key.
 */
export function rotationProblem(
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

/** One line of an open row's "What Burn did", with its key set apart. */
export interface DidItem {
  key?: string;
  text: string;
}

/**
 * An open row's "What Burn did", a line per step: the badge, each of its
 * own keys rotated (or the rotation that failed), each shared key flagged
 * and where its flag stands.
 */
export function didItems(
  incident: Incident,
  events: LedgerEvent[],
  flags: Flag[],
): DidItem[] {
  const { agent } = incident;
  const items: DidItem[] = [{ text: "Badge revoked" }];
  for (const ref of incident.rotated)
    items.push({ key: keyName(ref), text: "rotated" });
  const problem = rotationProblem(agent, events);
  if (problem === "failed")
    items.push({ text: "Rotation failed: finish rotating its keys by hand" });
  if (problem === "deferred")
    items.push({
      text: "Rotation deferred while other sessions use its keys",
    });
  const states = new Map(flags.map((flag) => [flag.secret_ref, flag.state]));
  for (const ref of incident.flagged) {
    const state = incident.openKeys.includes(ref) ? "open" : states.get(ref);
    items.push({
      key: keyName(ref),
      text:
        state === "open"
          ? "flagged for its owner, still open"
          : state === "dismissed"
            ? "flagged for its owner, dismissed"
            : "flagged for its owner",
    });
  }
  if (!incident.flagged.length && revokedEventFor(agent, events))
    items.push({ text: "No shared key to flag" });
  return items;
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
  /** Burns whose events don't name who or what started them. */
  unknown: number;
}

/** "How burns started": one count per burned agent, so they sum to the rows. */
export function causeCounts(causes: BurnCause[]): CauseCounts {
  const counts: CauseCounts = { canary: 0, person: 0, source: 0, unknown: 0 };
  for (const { trigger, source } of causes) {
    if (trigger.kind === "canary") counts.canary += 1;
    else if (source) counts.source += 1;
    else if (trigger.kind === "person") counts.person += 1;
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
