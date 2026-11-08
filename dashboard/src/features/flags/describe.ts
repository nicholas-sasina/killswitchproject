/**
 * Flagged keys in words. A flag is one credential an agent saw but does not
 * own, waiting for its owner to rotate it or dismiss it with a reason
 * (contracts: Flag, FlagResolution). Used by FlagActions, the Incidents
 * page's "Still open" card and the Credentials page.
 */
import { ApiError } from "../../api";
import type {
  Flag,
  FlagEvidence,
  FlagLevel,
  LedgerEvent,
} from "../../contracts";
import type { ConsoleAgent, ConsoleModel } from "../../model";
import { agentForEvent, keyName } from "../../model";

/** Weakest first: seen < attempted < reported. A flag only ever goes up. */
export const LEVELS: FlagLevel[] = ["seen", "attempted", "reported"];

export function levelRank(level: FlagLevel | null | undefined): number {
  return level ? LEVELS.indexOf(level) + 1 : 0;
}

/** What each level means, from the minter's own definition. */
export const LEVEL_MEANING: Record<FlagLevel, string> = {
  seen: "it entered an agent's context",
  attempted: "a check stopped it leaving",
  reported: "a monitored service saw it presented",
};

/** A flag on a key no single agent owns. */
export function isShared(flag: Pick<Flag, "owner_agent_id">): boolean {
  return !flag.owner_agent_id || flag.owner_agent_id === "shared";
}

/** The small capitals above a flagged key: "Shared key / seen". */
export function flagTag(flag: Flag): string {
  return `${isShared(flag) ? "Shared key" : "Agent key"} / ${flag.flag_level}`;
}

/** The reason rule the API enforces: 3 to 300 characters. */
export const REASON_MIN = 3;
export const REASON_MAX = 300;

/** What is wrong with a reason before it is sent, or undefined. */
export function reasonProblem(reason: string): string | undefined {
  const length = reason.trim().length;
  if (length < REASON_MIN)
    return `Write a reason of at least ${REASON_MIN} characters. It goes in the event feed.`;
  if (length > REASON_MAX)
    return `Keep the reason to ${REASON_MAX} characters or fewer.`;
  return undefined;
}

const BY = /^[A-Za-z0-9 ._@-]{1,64}$/;

/**
 * Who decided, as the API accepts it. An email with characters outside the
 * pattern is left out rather than mangled; the API records the signed-in
 * person as the event's actor either way.
 */
export function safeBy(email: string | null | undefined): string | undefined {
  const value = email?.trim();
  return value && BY.test(value) ? value : undefined;
}

/** A failed /flags/resolve in plain words. */
export function resolveError(error: unknown, key: string): string {
  if (error instanceof ApiError) {
    if (error.status === 400)
      return "Burn refused this reason. Write 3 to 300 characters, and never include a key's value.";
    if (error.status === 404)
      return `${key} is no longer flagged. Someone may have resolved it already.`;
    if (error.status === 409)
      return `The flag on ${key} changed while you were deciding. Close this and check it again.`;
  }
  return error instanceof Error && error.message
    ? error.message
    : "The decision did not reach Burn. Try again.";
}

/** After a dismissal: what would open the flag again. */
export function reopenNote(level: FlagLevel): string {
  const above = LEVELS.slice(levelRank(level));
  if (!above.length)
    return "Nothing reopens it: reported is already the highest level.";
  return `It reopens only if evidence at ${above.join(" or ")} arrives.`;
}

/** The flag as it stands after a decision, until the next poll confirms it. */
export function decided(
  flag: Flag,
  action: "rotate" | "dismiss",
  reason: string,
  by: string | undefined,
  at: string,
): Flag {
  return {
    ...flag,
    state: action === "rotate" ? "rotated" : "dismissed",
    resolution: { action, reason, by: by ?? null, at, level: flag.flag_level },
  };
}

export interface FirstEvidence {
  level: FlagLevel;
  evidence: FlagEvidence;
  /** When it was recorded, if the API said. */
  at?: string;
}

/**
 * The earliest evidence behind a flag. Each level keeps only its first
 * evidence, so the earliest of those is the first evidence of all. Without
 * times, the lowest level counts as first.
 */
export function firstEvidence(flag: Flag): FirstEvidence | undefined {
  const found: FirstEvidence[] = [];
  for (const level of LEVELS) {
    const evidence = flag.evidence?.[level];
    if (!evidence) continue;
    found.push({
      level,
      evidence,
      at: evidence.ts ?? evidence.recorded_at ?? undefined,
    });
  }
  if (!found.length) return undefined;
  const timed = found.filter((item) => item.at);
  const first =
    timed.length === found.length
      ? timed.reduce((a, b) => ((b.at as string) < (a.at as string) ? b : a))
      : found[0];
  // The first flag's time is the first evidence's, when the evidence has none.
  return first.at || first !== found[0]
    ? first
    : { ...first, at: flag.flagged_at };
}

/** "worker-07 was burned holding it": what the evidence shows, in words. */
export function evidenceSentence(
  evidence: FlagEvidence,
  name: string | undefined,
): string {
  const who = name ?? "An agent";
  const tool = evidence.tool ? ` through ${evidence.tool}` : "";
  switch (evidence.source) {
    case "burn":
      return `${who} was burned holding it`;
    case "tool_response":
      return `${who} saw it${tool || " in a tool response"}`;
    case "handoff":
      return `${who} was handed it in a message`;
    case "egress":
      return `${who} tried to send it out; the egress proxy stopped it`;
    case "gateway":
      return `${who} tried to pass it in a tool call${tool}; Burn stopped it`;
    case "provider":
      return name
        ? `Presented at a monitored service after ${name} saw it`
        : "Presented at a monitored service";
    default:
      return name ? `${name} saw it` : "Flagged";
  }
}

/** The name of the agent a piece of evidence is about, if the fleet knows it. */
export function evidenceAgent(
  model: ConsoleModel,
  evidence: FlagEvidence,
): ConsoleAgent | undefined {
  if (!evidence.agent_id && !evidence.session_id) return undefined;
  return agentForEvent(model, {
    agent_id: evidence.agent_id ?? "",
    session_id: evidence.session_id ?? null,
  });
}

const SAW: ReadonlySet<string> = new Set([
  "recorded",
  "inherited",
  "flagged",
  "blocked",
]);

/**
 * Which agents saw each key, from the loaded events: a tool response that
 * carried it, a message that handed it over, a burn that flagged it, or a
 * check that stopped it leaving. One pass over the stream; keys by
 * reference, agents in the order they first saw it.
 */
export function seenByKey(
  events: LedgerEvent[],
  model: ConsoleModel,
  refs: ReadonlySet<string>,
): Map<string, ConsoleAgent[]> {
  const found = new Map<string, Map<string, ConsoleAgent>>();
  if (!refs.size) return new Map();
  // Events come newest first; walk oldest first so order is first-seen.
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    const ref = event.secret_ref;
    if (!ref || !refs.has(ref) || !SAW.has(event.action)) continue;
    if (event.agent_id === "system") continue;
    const agent = agentForEvent(model, event);
    if (!agent) continue;
    let agents = found.get(ref);
    if (!agents) found.set(ref, (agents = new Map()));
    if (!agents.has(agent.key)) agents.set(agent.key, agent);
  }
  return new Map(
    [...found].map(([ref, agents]) => [ref, [...agents.values()]]),
  );
}

/** "crm_db_url", or the reference itself when it has no name part. */
export function flagKey(flag: Pick<Flag, "secret_ref">): string {
  return keyName(flag.secret_ref) || flag.secret_ref;
}

/**
 * A key its owner rotated, from the event Burn writes for the decision.
 * GET /flags no longer lists a rotated key, so this is where it shows.
 */
export interface OwnerRotation {
  ref: string;
  at: string;
  actor?: string | null;
  /** The owner's reason, from the event's detail when it carries one. */
  reason?: string;
  /** The flag's level when its owner rotated it, from the same detail. */
  level?: FlagLevel;
}

/**
 * Owner rotations in the loaded events, newest first, one per key. Burn
 * writes them as `rotated` on the agent id "system", with the detail
 * "<who> rotated this flagged credential (was <level>, <n> instance(s)):
 * <reason>".
 */
export function ownerRotations(events: LedgerEvent[]): OwnerRotation[] {
  const seen = new Set<string>();
  const list: OwnerRotation[] = [];
  for (const event of events) {
    if (event.action !== "rotated" || event.agent_id !== "system") continue;
    const ref = event.secret_ref;
    if (!ref || seen.has(ref)) continue;
    seen.add(ref);
    const reason = event.detail?.match(/\binstance\(s\)\): (.+)$/s)?.[1];
    const level = event.detail?.match(/\(was (seen|attempted|reported),/)?.[1];
    list.push({
      ref,
      at: event.ts,
      actor: event.actor,
      reason,
      ...(level ? { level: level as FlagLevel } : {}),
    });
  }
  return list;
}
