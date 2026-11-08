/**
 * What a burn will do and what it did, as plain lines, from real data only.
 *
 * Before a burn, `agentPlan` and `rolePlan` list exactly what will happen:
 * the agent's own keys by name, the shared keys it saw in plain text, the
 * children it leaves running. After one, `containmentSteps` turns the API's
 * answer into the checklist that ticks: revoked, each owned key rotated, the
 * shared keys sent to their owners, everyone else untouched. A step that did
 * not finish says so and says what to do.
 *
 * The rules mirror the API's own (burn_api/lambdas/api/burn.py): a burn
 * rotates the agent's own credentials, and flags only the keys it does not
 * own whose plain text reached it; a key it only ever saw as a reference is
 * never flagged, and a canary is never flagged.
 */
import type { BurnResult, LedgerEntry } from "../../contracts";
import type { ConsoleAgent, ConsoleModel } from "../../model/console";
import { isCanaryRef, keyName } from "../../model/keys";
import { plural } from "../../plural";
import type { BurnOutcome, ContainmentStep } from "./types";

// ---------- Keys ----------

export type KeyKind = "canary" | "owned" | "shared" | "unknown";

/** Whose key a ledger entry is, from the agent's point of view. */
export function keyKind(
  entry: Pick<LedgerEntry, "secret_ref" | "owner" | "owner_agent_id">,
  agentId: string,
): KeyKind {
  if (isCanaryRef(entry.secret_ref)) return "canary";
  if (entry.owner === "unknown") return "unknown";
  if (
    entry.owner === "agent" &&
    (!entry.owner_agent_id || entry.owner_agent_id === agentId)
  )
    return "owned";
  // A team's shared key, or another agent's own key: not its to rotate.
  return "shared";
}

/** Whether the key's plain text reached the agent (not just a reference). */
export function inPlainText(entry: Pick<LedgerEntry, "exposure">): boolean {
  return entry.exposure !== "referenced";
}

/**
 * The credential types an agent is minted: its role's, narrowed to its
 * parent's when another agent started it (a child never gets more).
 */
export function credentialsFor(
  agent: ConsoleAgent,
  model: ConsoleModel,
): string[] {
  const own = model.roleByName.get(agent.role)?.role?.credentials ?? [];
  if (!agent.parent) return [...own];
  const parent = model.byKey.get(agent.parent);
  const theirs = parent
    ? model.roleByName.get(parent.role)?.role?.credentials
    : undefined;
  return theirs ? own.filter((kind) => theirs.includes(kind)) : [...own];
}

/** The keys a burn rotates, by name: its own, from the ledger and its role. */
export function ownedKeys(
  entries: LedgerEntry[] | undefined,
  agentId: string,
  credentials: readonly string[] = [],
): string[] {
  const names = new Set<string>();
  for (const entry of entries ?? [])
    if (entry.secret_ref && keyKind(entry, agentId) === "owned")
      names.add(keyName(entry.secret_ref));
  for (const kind of credentials) names.add(kind);
  return [...names].sort();
}

/**
 * A key's name as this agent should read it: "crm_db_url" for a shared key
 * or its own, "agent-analyst-1/api_key" for another agent's own key, so it
 * can't be mistaken for its own api_key.
 */
export function keyLabel(ref: string, agentId: string): string {
  const parts = ref.split("/").filter(Boolean);
  const owner = parts.length >= 3 ? parts.at(-2) : undefined;
  return owner && owner !== "shared" && owner !== agentId
    ? `${owner}/${keyName(ref)}`
    : keyName(ref);
}

/** The keys a burn flags, by name: shared keys it saw in plain text. */
export function plainSharedKeys(
  entries: LedgerEntry[] | undefined,
  agentId: string,
): string[] {
  const names = new Set<string>();
  for (const entry of entries ?? [])
    if (
      entry.secret_ref &&
      keyKind(entry, agentId) === "shared" &&
      inPlainText(entry)
    )
      names.add(keyLabel(entry.secret_ref, agentId));
  return [...names].sort();
}

// ---------- Words ----------

const NUMBER_WORDS = [
  "no",
  "one",
  "two",
  "three",
  "four",
  "five",
  "six",
  "seven",
  "eight",
  "nine",
];

/** "one", "two" … "nine", then digits. */
export function numberWord(n: number): string {
  return NUMBER_WORDS[n] ?? String(n);
}

/** "a", "a and b", "a, b and c". */
export function listWords(items: readonly string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

/** An aws_key is replaced (a new access key); anything else is rotated. */
function isAwsKey(name: string): boolean {
  return /aws/i.test(name);
}

function stepKeyName(ref: string): string {
  return isCanaryRef(ref) ? "Its canary" : keyName(ref) || "A key";
}

// ---------- Before a burn: what will happen ----------

/** A part of a plan line: plain words, or a key name set in mono. */
export type PlanPart = string | { key: string };

export interface PlanLine {
  id: string;
  parts: PlanPart[];
}

/** Key names joined as words: [a] and [b]; [a], [b] and [c]. */
function keyParts(names: readonly string[]): PlanPart[] {
  const parts: PlanPart[] = [];
  names.forEach((name, index) => {
    if (index > 0) parts.push(index === names.length - 1 ? " and " : ", ");
    parts.push({ key: name });
  });
  return parts;
}

/** The plan as one string, for tests and accessible summaries. */
export function planText(lines: PlanLine[]): string[] {
  return lines.map((line) =>
    line.parts
      .map((part) => (typeof part === "string" ? part : part.key))
      .join(""),
  );
}

/**
 * What burning one agent will do. `entries` is its ledger, undefined until
 * it loads (the lines then speak generally rather than guess).
 */
export function agentPlan({
  agent,
  entries,
  credentials = [],
  childrenRunning = [],
  replaceOnBurn = false,
}: {
  agent: Pick<ConsoleAgent, "agentId" | "role" | "sessionId">;
  entries?: LedgerEntry[];
  credentials?: readonly string[];
  /** Names of the agents it started that are still running. */
  childrenRunning?: readonly string[];
  replaceOnBurn?: boolean;
}): PlanLine[] {
  const lines: PlanLine[] = [
    { id: "revoke", parts: ["Revoke its badge. Its next call is refused."] },
  ];
  const owned = ownedKeys(entries, agent.agentId, credentials);
  if (owned.length) {
    const one = owned.length === 1;
    lines.push({
      id: "rotate",
      parts: [
        "Rotate ",
        ...keyParts(owned),
        `, the ${numberWord(owned.length)} ${one ? "key" : "keys"} it owns. The old ${one ? "value stops" : "values stop"} working at once.`,
      ],
    });
  } else if (entries) {
    lines.push({
      id: "rotate",
      parts: ["It owns no keys, so nothing is rotated."],
    });
  } else {
    lines.push({
      id: "rotate",
      parts: ["Rotate the keys it owns. The old values stop working at once."],
    });
  }
  const shared = plainSharedKeys(entries, agent.agentId);
  if (shared.length) {
    lines.push({
      id: "flag",
      parts: [
        "Flag ",
        ...keyParts(shared),
        `, which it saw in plain text, for ${shared.length === 1 ? "its owner" : "their owners"}.`,
      ],
    });
  } else if (entries) {
    lines.push({
      id: "flag",
      parts: ["It saw no shared key in plain text, so nothing is flagged."],
    });
  } else {
    lines.push({
      id: "flag",
      parts: ["Flag the shared keys it saw in plain text, for their owners."],
    });
  }
  lines.push({
    id: "others",
    parts: [
      childrenRunning.length
        ? `Leave its ${childrenRunning.length === 1 ? "child" : "children"} ${listWords(childrenRunning)} running, and every other agent.`
        : "Leave every other agent running.",
    ],
  });
  // Only a burn of the one session marks it for a replacement.
  if (replaceOnBurn && agent.sessionId)
    lines.push({
      id: "replace",
      parts: [
        `Mark it for a replacement. A fresh ${agent.role} resumes the work it finished; anything it touched after the first evidence waits for a person.`,
      ],
    });
  return lines;
}

/** What burning every running agent of a role will do. */
export function rolePlan({
  role,
  running,
  credentials = [],
  childrenElsewhere = [],
  replaceOnBurn = false,
}: {
  role: string;
  /** Names of the role's running agents. */
  running: readonly string[];
  credentials?: readonly string[];
  /** Running agents of other roles that these agents started. */
  childrenElsewhere?: readonly string[];
  replaceOnBurn?: boolean;
}): PlanLine[] {
  const n = running.length;
  if (!n)
    return [
      {
        id: "none",
        parts: [`No ${role}s are running, so there is nothing to burn.`],
      },
    ];
  const lines: PlanLine[] = [
    {
      id: "revoke",
      parts: [
        n === 1
          ? `Revoke the badge of its one running ${role}, ${running[0]}. Its next call is refused.`
          : `Revoke the badges of all ${n} running ${role}s. Their next calls are refused.`,
      ],
    },
  ];
  const kinds = [...new Set(credentials)].sort();
  if (kinds.length)
    lines.push({
      id: "rotate",
      parts: [
        n === 1 ? "Rotate its own " : "Rotate each one's own ",
        ...keyParts(kinds),
        ". The old values stop working at once.",
      ],
    });
  lines.push({
    id: "flag",
    parts: [
      n === 1
        ? "Flag the shared keys it saw in plain text, for their owners."
        : "Flag the shared keys any of them saw in plain text, for their owners.",
    ],
  });
  if (childrenElsewhere.length)
    lines.push({
      id: "children",
      parts: [
        `Leave ${listWords(childrenElsewhere)} running: ${childrenElsewhere.length === 1 ? "it is" : "they are"} another role, though ${n === 1 ? "it" : "one of them"} started ${childrenElsewhere.length === 1 ? "it" : "them"}.`,
      ],
    });
  lines.push({ id: "others", parts: ["Leave every other role running."] });
  if (replaceOnBurn)
    lines.push({
      id: "replace",
      parts: ["Start no replacements: a role burn never replaces its agents."],
    });
  return lines;
}

// ---------- After a burn: the checklist ----------

export interface StepContext {
  /** A burned agent's display name, by agent id. */
  nameOf?: (agentId: string) => string | undefined;
  /**
   * How many running agents the burn left alone, when that is known (a burn
   * made from this console). Unknown for an older burn.
   */
  untouched?: number;
  /** The agent's children still running after it was burned. */
  childrenLeft?: readonly string[];
}

/**
 * The checklist for a burn, in the order it happened: revoked, each owned
 * key rotated, the shared keys sent to their owners, everyone else
 * untouched. Built from the API's real lists; a part that did not finish
 * carries an `error` saying what to do.
 */
export function containmentSteps(
  outcome: Pick<BurnOutcome, "target" | "burned" | "failed">,
  context: StepContext = {},
): ContainmentStep[] {
  return outcome.target.scope === "agent"
    ? agentSteps(outcome, context)
    : roleSteps(outcome, outcome.target.role, context);
}

function untouchedTitle(n: number | undefined, fallback: string): string {
  if (n === undefined) return fallback;
  return n === 0
    ? "No other agents were running"
    : `${plural(n, "agent")} untouched`;
}

function agentSteps(
  outcome: Pick<BurnOutcome, "target" | "burned" | "failed">,
  context: StepContext,
): ContainmentStep[] {
  const name =
    outcome.target.scope === "agent" ? outcome.target.agent.name : "it";
  const result: BurnResult | undefined = outcome.burned[0];
  const failure = outcome.failed[0];
  const steps: ContainmentStep[] = [
    {
      id: "revoked",
      title: "Badge revoked",
      sub: "Its next call is refused",
      hot: true,
      error:
        result && !result.revoked
          ? `Burn couldn't confirm the revocation, so its calls may still go through. Burn ${name} again.`
          : !result && !failure
            ? `Burn found nothing running to revoke. It may already be burned; check its status.`
            : undefined,
    },
  ];
  for (const ref of result?.rotated ?? []) {
    const key = stepKeyName(ref);
    steps.push({
      id: `rotated:${ref}`,
      title: `${key} ${isAwsKey(key) ? "replaced" : "rotated"}`,
      sub: isAwsKey(key)
        ? "The old key is inactive"
        : "The old value no longer works",
    });
  }
  for (const ref of result?.flagged ?? [])
    steps.push({
      id: `flagged:${ref}`,
      title: `${stepKeyName(ref)} sent to its owner`,
      sub: "Shared; its owner decides whether to rotate",
    });
  if (failure)
    steps.push({
      id: "unfinished",
      title: "Keys not rotated",
      error: `Didn't finish${failure.error ? `: ${failure.error}` : ""}. Its badge is revoked; burn ${name} again to rotate its keys and flag what it saw.`,
    });
  else if (result && !result.rotated.length && !result.flagged.length)
    steps.push({
      id: "keys",
      title: "Nothing to rotate or flag",
      sub: "It owned no keys and saw no shared key in plain text",
    });
  const children = context.childrenLeft ?? [];
  steps.push({
    id: "untouched",
    title: untouchedTitle(context.untouched, "Every other agent untouched"),
    sub: children.length
      ? `Including its ${children.length === 1 ? "child" : "children"}, ${listWords(children)}`
      : "A burn stops only this agent",
  });
  return steps;
}

function roleSteps(
  outcome: Pick<BurnOutcome, "burned" | "failed">,
  role: string,
  context: StepContext,
): ContainmentStep[] {
  const nameOf = (agentId?: string) =>
    (agentId && context.nameOf?.(agentId)) || agentId || `a ${role}`;
  const { burned, failed } = outcome;
  const total = burned.length + failed.length;
  const unconfirmed = burned.filter((result) => !result.revoked);
  const steps: ContainmentStep[] = [
    {
      id: "revoked",
      title: total ? `${plural(total, role)} revoked` : "Nothing to revoke",
      sub: total
        ? total === 1
          ? "Its next call is refused"
          : "Their next calls are refused"
        : `No ${role} was running`,
      hot: true,
      error: unconfirmed.length
        ? `${listWords(unconfirmed.map((result) => nameOf(result.agent_id)))} not confirmed revoked. Burn ${unconfirmed.length === 1 ? "it" : "each one"} again from ${unconfirmed.length === 1 ? "its" : "their"} page.`
        : undefined,
    },
  ];
  const rotated = burned.flatMap((result) => result.rotated);
  if (rotated.length) {
    const kinds = [...new Set(rotated.map((ref) => keyName(ref)))].sort();
    steps.push({
      id: "rotated",
      title: `${plural(rotated.length, "key")} rotated`,
      sub: `Each ${role}'s own ${listWords(kinds)}`,
    });
  }
  const flagged = [...new Set(burned.flatMap((result) => result.flagged))];
  if (flagged.length === 1)
    steps.push({
      id: "flagged",
      title: `${stepKeyName(flagged[0])} sent to its owner`,
      sub: "Shared; its owner decides whether to rotate",
    });
  else if (flagged.length > 1) {
    const names = flagged.map((ref) => keyName(ref)).sort();
    steps.push({
      id: "flagged",
      title: `${plural(flagged.length, "shared key")} sent to their owners`,
      sub:
        names.length > 4
          ? `${names.slice(0, 4).join(", ")} and ${names.length - 4} more`
          : listWords(names),
    });
  }
  if (failed.length)
    steps.push({
      id: "unfinished",
      title: `${plural(failed.length, role)} not finished`,
      error: `${listWords(failed.map((entry) => nameOf(entry.agent_id)))} didn't finish. Their badges are revoked; burn each one again from its page to rotate its keys.`,
    });
  steps.push({
    id: "untouched",
    title: untouchedTitle(context.untouched, "Every other role untouched"),
    sub: "A role burn stops only its own agents",
  });
  return steps;
}
