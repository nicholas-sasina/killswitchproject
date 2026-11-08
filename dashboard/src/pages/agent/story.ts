/**
 * An agent's story in plain sentences, from live data only: the hero's one
 * sentence, its timeline, what happened after the burn
 * (its replacement and its keys' CloudTrail calls) and what a burn would do.
 */
import type {
  BlastRadius,
  LedgerEntry,
  LedgerEvent,
  Role,
} from "../../contracts";
import {
  listWords,
  numberWord,
  ownedKeys,
  plainSharedKeys,
} from "../../features/burn/steps";
import type { ContainmentStep } from "../../features/burn/types";
import type { ConsoleAgent, ConsoleModel } from "../../model/console";
import type { BurnTrigger } from "../../model/burns";
import { isCanaryRef, keyName } from "../../model/keys";
import { personLabel } from "../../model/people";
import {
  eventSentence,
  withoutSubject,
  type SentenceContext,
} from "../../model/sentences";
import { clockTime, dayTime, duration } from "../../model/time";
import { plural } from "../../plural";
import { ledgerRows } from "./ledger";

// ---------- The agent's events ----------

/**
 * The events about this agent, newest first. An instance has its own agent
 * id, so its id is enough; a scenario agent's id spans its sessions, so
 * only this session's events (or unsessioned ones since it started) count.
 */
export function agentEvents(
  events: LedgerEvent[],
  agent: ConsoleAgent,
  model: ConsoleModel,
): LedgerEvent[] {
  const shared = (model.byAgentId.get(agent.agentId)?.length ?? 1) > 1;
  return events.filter(
    (event) =>
      event.agent_id === agent.agentId &&
      (!shared ||
        (event.session_id
          ? event.session_id === agent.sessionId
          : !agent.createdAt || event.ts >= agent.createdAt)),
  );
}

// ---------- The hero's sentence ----------

function article(word: string): string {
  return /^[aeiou]/i.test(word) ? "An" : "A";
}

function startedSentence(
  agent: ConsoleAgent,
  model: ConsoleModel,
): string | undefined {
  const when = dayTime(agent.createdAt, model.now);
  const parent = agent.parent ? model.byKey.get(agent.parent) : undefined;
  const by = personLabel(agent.createdBy) || parent?.name;
  if (!when) return by ? `Started by ${by}.` : undefined;
  return by ? `Started by ${by} at ${when}.` : `Started at ${when}.`;
}

/** What it saw and tried, in one sentence, from its ledger and events. */
export function evidenceSentence(
  entries: LedgerEntry[] | undefined,
  agent: ConsoleAgent,
  events: LedgerEvent[],
): string | undefined {
  if (!entries) return undefined;
  const rows = ledgerRows(entries, {
    agentId: agent.agentId,
    events,
    burned: false,
  });
  if (rows.some((row) => row.kind === "canary" && row.hot))
    return "Its canary was used outside Burn.";
  const shared = plainSharedKeys(entries, agent.agentId).length;
  const blocked = rows.filter((row) => row.evidence.includes("blocked"));
  const tried = blocked.filter((row) => row.kind === "shared").length;
  // Keys other than the shared ones it tried to send, by name.
  const others = listWords(
    blocked.filter((row) => row.kind !== "shared").map((row) => row.name),
  );
  if (shared)
    return `It saw ${numberWord(shared)} shared ${shared === 1 ? "key" : "keys"} in plain text${
      tried
        ? shared === 1
          ? " and tried to send it out."
          : ` and tried to send ${numberWord(tried)} out.`
        : others
          ? `, and tried to send ${others} out.`
          : "."
    }`;
  if (others) return `It tried to send ${others} out; Burn stopped it.`;
  const seen = entries.filter((entry) => !isCanaryRef(entry.secret_ref));
  if (seen.length && seen.every((entry) => entry.exposure === "referenced"))
    return `It has seen ${numberWord(seen.length)} ${seen.length === 1 ? "key" : "keys"}, only as references.`;
  return undefined;
}

/**
 * One plain sentence on what it is and its state:
 *   "A worker, one of 14. Running for 30 minutes. Started by … at 7:12 PM.
 *    It saw one shared key in plain text and tried to send it out."
 *   "A worker. Burned automatically at 7:42:09 PM when the decoy key in its
 *    session was used from outside Burn. Started by … at 7:12 PM."
 */
export function heroLede({
  agent,
  model,
  entries,
  events,
  cause,
  burnedAt,
}: {
  agent: ConsoleAgent;
  model: ConsoleModel;
  entries?: LedgerEntry[];
  /** This agent's events. */
  events: LedgerEvent[];
  /** Why it was burned (`burnTrigger`, or "you" for a burn from this page). */
  cause: BurnTrigger;
  /** When a burn made from this page was answered, before its events load. */
  burnedAt?: string;
}): string {
  const kind = `${article(agent.role)} ${agent.role}`;
  const started = startedSentence(agent, model);
  const parts: string[] = [];
  if (agent.state === "running") {
    const running = model.roleByName.get(agent.role)?.running.length ?? 0;
    parts.push(running > 1 ? `${kind}, one of ${running}.` : `${kind}.`);
    const age = duration(agent.createdAt, model.now);
    if (age) parts.push(`Running for ${age}.`);
    if (started) parts.push(started);
    const evidence = evidenceSentence(entries, agent, events);
    if (evidence) parts.push(evidence);
  } else if (agent.state === "burned") {
    parts.push(`${kind}.`);
    const estimated = !burnedAt && agent.burnedAtFrom === "last-seen";
    const time = clockTime(burnedAt ?? agent.burnedAt);
    const at = time && !estimated ? ` at ${time}` : "";
    if (cause.kind === "canary")
      parts.push(
        `Burned automatically${at} when the decoy key in its session was used from outside Burn.`,
      );
    else if (cause.kind === "person")
      parts.push(
        `Burned by ${cause.who}${at}${cause.reason ? `: ${cause.reason}.` : "."}`,
      );
    else if (cause.kind === "reason")
      parts.push(`Burned${at}: ${cause.reason}.`);
    else parts.push(`Burned${at}. No reason was recorded.`);
    if (estimated && time) parts.push(`Its last call was at ${time}.`);
    if (started) parts.push(started);
  } else {
    parts.push(`${kind}.`);
    const end = agent.instance?.exited_at ?? agent.lastSeen;
    const when = dayTime(end, model.now);
    const took =
      agent.createdAt && end ? duration(agent.createdAt, new Date(end)) : "";
    if (when) parts.push(`Finished at ${when}${took ? ` after ${took}` : ""}.`);
    if (started) parts.push(started);
  }
  return parts.join(" ");
}

// ---------- Timeline ----------

export interface TimelineItem {
  id: string;
  at: string;
  text: string;
  /** routine: grey mark; evidence: ember mark; burn: the burn itself; after: ink. */
  tone: "routine" | "evidence" | "burn" | "after";
}

/** Burn's own bookkeeping after a burn lives in the Containment card. */
function isBurnOutcome(event: LedgerEvent): boolean {
  return (
    event.source === "burn" &&
    (event.action === "rotated" || event.action === "flagged")
  );
}

/**
 * Its events as sentences, newest first. Repeats collapse into one line
 * with a count: the same key seen again, the same leak blocked again, and
 * every call refused after the burn.
 */
export function timelineItems(
  events: LedgerEvent[],
  context: SentenceContext,
  burnedAt?: string,
  /** Keys that are evidence in its ledger: reading one is marked too. */
  evidenceRefs: ReadonlySet<string> = new Set(),
): TimelineItem[] {
  const groups = new Map<string, { item: TimelineItem; count: number }>();
  const order: string[] = [];
  const oldestFirst = [...events]
    .filter((event) => !isBurnOutcome(event))
    .sort((a, b) => a.event_id.localeCompare(b.event_id));
  for (const event of oldestFirst) {
    const afterBurn =
      event.action === "refused" &&
      burnedAt !== undefined &&
      event.ts >= burnedAt;
    const group = afterBurn
      ? "refused-after-burn"
      : event.action === "recorded" || event.action === "inherited"
        ? `${event.action}:${event.secret_ref}`
        : event.action === "blocked"
          ? `blocked:${event.secret_ref}:${event.destination}`
          : event.action === "rejected"
            ? `rejected:${event.secret_ref}`
            : event.action === "refused"
              ? `refused:${event.detail}:${event.tool}`
              : event.event_id;
    const known = groups.get(group);
    if (known) {
      known.count += 1;
      continue;
    }
    const sentence = eventSentence(event, context);
    groups.set(group, {
      count: 1,
      item: {
        id: event.event_id,
        at: event.ts,
        text: withoutSubject(sentence),
        tone:
          event.action === "revoked"
            ? "burn"
            : afterBurn || event.action === "rejected"
              ? "after"
              : sentence.evidence ||
                  (event.action === "recorded" &&
                    evidenceRefs.has(event.secret_ref ?? ""))
                ? "evidence"
                : "routine",
      },
    });
    order.push(group);
  }
  return order
    .map((key) => {
      const { item, count } = groups.get(key)!;
      if (key === "refused-after-burn")
        return {
          ...item,
          text: `Its calls were refused ${count === 1 ? "once" : `${count} times`} after the burn`,
        };
      return count > 1
        ? { ...item, text: `${item.text} (${count} times)` }
        : item;
    })
    .reverse();
}

// ---------- Before a burn ----------

/** "If you burn it": what a burn would do, greyed until it happens. */
export function beforeBurnSteps({
  agentId,
  entries,
  credentials,
  othersRunning,
  childrenRunning,
}: {
  agentId: string;
  entries?: LedgerEntry[];
  credentials: readonly string[];
  othersRunning: number;
  childrenRunning: readonly string[];
}): ContainmentStep[] {
  const steps: ContainmentStep[] = [
    {
      id: "revoke",
      title: "Revoke its badge",
      sub: "Its next call is refused",
      hot: true,
    },
  ];
  for (const name of ownedKeys(entries, agentId, credentials)) {
    const aws = /aws/i.test(name);
    steps.push({
      id: `rotate:${name}`,
      title: `${aws ? "Replace" : "Rotate"} ${name}`,
      sub: aws
        ? "The old key goes inactive"
        : "Its own copy; nobody else uses it",
    });
  }
  for (const name of plainSharedKeys(entries, agentId))
    steps.push({
      id: `flag:${name}`,
      title: `Flag ${name}`,
      sub: "Shared; its owner decides",
    });
  steps.push({
    id: "others",
    title: "Leave everyone else running",
    sub: othersRunning
      ? `${plural(othersRunning, "agent")}${childrenRunning.length ? `, including ${listWords(childrenRunning)}` : ""}`
      : "No other agents are running",
  });
  return steps;
}

// ---------- After a burn: its replacement ----------

export interface Handover {
  headline: string;
  /** The other agent, to link to. */
  other?: { name: string; key: string };
  detail?: string;
  quarantined: string[];
  quarantineLabel: string;
}

function itemsPhrase(done: number, quarantined: number): string {
  const finished = done
    ? `${numberWord(done)} ${done === 1 ? "item" : "items"} finished before it ${done === 1 ? "counts" : "count"} as done`
    : "nothing finished before it";
  const held = quarantined
    ? `${numberWord(quarantined)} in progress or touched after it ${quarantined === 1 ? "is" : "are"} quarantined for a person`
    : "nothing is quarantined";
  return `${finished}; ${held}.`;
}

/** A burned agent's replacement, from the #105 record on its instance. */
export function replacementOf(
  agent: ConsoleAgent,
  model: ConsoleModel,
  role?: Role,
): Handover | undefined {
  const instance = agent.instance;
  if (!instance) return undefined;
  const record = instance.replacement;
  const label = "Quarantined when it was burned";
  if (!record) {
    return {
      headline: "Not replaced",
      detail:
        role?.replace_on_burn === false
          ? `The ${agent.role} role doesn't replace burned agents.`
          : "Burn marks an agent for a replacement only when it is burned on its own.",
      quarantined: [],
      quarantineLabel: label,
    };
  }
  const next = instance.replaced_by
    ? model.byKey.get(instance.replaced_by)
    : undefined;
  const cutoff = clockTime(record.cutoff);
  const detail = `${cutoff ? `Cut-off ${cutoff}, ${record.cutoff_source === "evidence" ? "the first evidence" : "the burn time"}. ` : ""}${itemsPhrase(record.done.length, record.quarantined.length).replace(/^./, (c) => c.toUpperCase())}`;
  const base = { quarantined: record.quarantined, quarantineLabel: label };
  if (instance.replaced_by)
    return {
      ...base,
      headline: `${next?.name ?? instance.replaced_by} took over`,
      other: {
        name: next?.name ?? instance.replaced_by,
        key: instance.replaced_by,
      },
      detail,
    };
  if (record.status === "needs_person")
    return {
      ...base,
      headline: "Not replaced: it needs a person",
      detail: `It was already a third-generation replacement, so Burn won't replace it again. ${detail}`,
    };
  return {
    ...base,
    headline: "A replacement is on its way",
    detail: `Whatever started it links a fresh ${agent.role} when one joins. ${detail}`,
  };
}

/** For a replacement: what it took over, from its predecessor's record. */
export function takeoverOf(
  agent: ConsoleAgent,
  model: ConsoleModel,
): Handover | undefined {
  const replaces = agent.instance?.replaces;
  if (!replaces) return undefined;
  const previous = model.byKey.get(replaces);
  const record = previous?.instance?.replacement;
  const started = previous?.instance?.items_started;
  const name = previous?.name ?? replaces;
  return {
    headline: `Took over from ${name}`,
    other: { name, key: replaces },
    detail: record
      ? `Resumed at ${record.done.length}${started !== undefined && started !== null ? ` of ${started}` : ""} items; ${record.quarantined.length} quarantined for a person.`
      : "Resume progress not reported.",
    quarantined: record?.quarantined ?? [],
    quarantineLabel: "Inherited quarantine",
  };
}

// ---------- After a burn: blast radius ----------

const CALL_WORDS: Record<string, [string, string]> = {
  InvokeModel: ["model call", "model calls"],
  InvokeModelWithResponseStream: ["model call", "model calls"],
  Converse: ["model call", "model calls"],
  ConverseStream: ["model call", "model calls"],
  ListBuckets: ["bucket list", "bucket lists"],
  GetObject: ["object read", "object reads"],
  PutObject: ["object write", "object writes"],
  GetCallerIdentity: ["identity check", "identity checks"],
};

function callWords(name: string, n: number): string {
  const [one, many] = CALL_WORDS[name] ?? [`${name} call`, `${name} calls`];
  return `${n} ${n === 1 ? one : many}`;
}

export const CLOUDTRAIL_LAG = "CloudTrail runs a few minutes behind.";

/**
 * What CloudTrail shows for its AWS keys, in one sentence:
 * "CloudTrail shows 3 calls with the old aws_key, the last at 7:41:55 PM:
 *  2 model calls and 1 bucket list."
 */
export function blastSentence(blast: BlastRadius): string {
  if (!blast.keys.length && !blast.events.length)
    return "It held no AWS keys, so CloudTrail has nothing to show.";
  if (!blast.events.length)
    return `CloudTrail shows no calls with its AWS ${blast.keys.length === 1 ? "key" : "keys"} yet. ${CLOUDTRAIL_LAG}`;
  const inactive = new Set(
    blast.keys
      .filter((key) => key.status === "inactive" && key.key_ref)
      .map((key) => key.key_ref!),
  );
  const names = [
    ...new Set(
      blast.events.map((event) => keyName(event.key_ref).replace(/#v\d+$/, "")),
    ),
  ].sort();
  const old = blast.events.every((event) => inactive.has(event.key_ref));
  const last = blast.events.reduce(
    (newest, event) => (event.ts > newest ? event.ts : newest),
    "",
  );
  const counts = new Map<string, number>();
  for (const event of blast.events)
    counts.set(event.event_name, (counts.get(event.event_name) ?? 0) + 1);
  // Calls with the same words (two kinds of model call) count together.
  const byWords = new Map<string, { name: string; n: number }>();
  for (const [name, n] of counts) {
    const word = CALL_WORDS[name]?.[0] ?? name;
    const known = byWords.get(word);
    byWords.set(word, { name: known?.name ?? name, n: (known?.n ?? 0) + n });
  }
  const kinds = [...byWords.values()].sort(
    (a, b) => b.n - a.n || a.name.localeCompare(b.name),
  );
  const shown = kinds.slice(0, 3).map((kind) => callWords(kind.name, kind.n));
  const rest = kinds.slice(3).reduce((sum, kind) => sum + kind.n, 0);
  if (rest) shown.push(`${rest} other ${rest === 1 ? "call" : "calls"}`);
  const refused = blast.events.filter((event) => event.error_code).length;
  return `CloudTrail shows ${plural(blast.events.length, "call")} with ${old ? "the old " : ""}${listWords(names)}${last ? `, the last at ${clockTime(last)}` : ""}: ${listWords(shown)}.${
    refused
      ? ` ${refused === blast.events.length ? "All were" : `${numberWord(refused).replace(/^./, (c) => c.toUpperCase())} ${refused === 1 ? "was" : "were"}`} refused.`
      : ""
  } ${CLOUDTRAIL_LAG}`;
}
