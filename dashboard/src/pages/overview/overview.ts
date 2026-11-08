/**
 * What the Overview says, worked out from the console's shared model and
 * event stream. Pure functions only, so each sentence and count is tested.
 *
 * The Overview shows what is running now and what was burned today. Every
 * count comes from the shared model (model/console.ts) or from the loaded
 * events; nothing here is invented, and a figure the API cannot give is
 * left out rather than estimated.
 */
import { isBlockedAtExit, isUnknownCaller } from "../../alerts";
import type { Flag, FlagLevel, LedgerEvent, Role } from "../../contracts";
import type { EventHistory } from "../../polling";
import { agentHref, roleHref } from "../../router";
import {
  agentForEvent,
  burnTrigger,
  canaryIp,
  dayTime,
  findAgent,
  isSameDay,
  keyName,
  shortTime,
  startOfDay,
  triggerShort,
  type BurnTrigger,
  type ConsoleAgent,
  type ConsoleModel,
  type Incident,
  type RoleGroup,
} from "../../model";

const DAY_MS = 24 * 60 * 60 * 1000;

// ---------- Words ----------

const WORDS = [
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
  "ten",
];

/** "two" for small counts, digits from 11 up. */
export function countWord(n: number): string {
  return WORDS[n] ?? String(n);
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function s(n: number, one: string, many = `${one}s`): string {
  return n === 1 ? one : many;
}

/** "a", "a and b", "a, b and c". */
export function joinList(parts: string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`;
}

/** End a sentence once, whatever the reason text already ends with. */
function sentence(text: string): string {
  return /[.!?]$/.test(text) ? text : `${text}.`;
}

// ---------- Evidence in the stream ----------

export interface EvidenceCounts {
  /** Credentials agents saw: first sightings in tool output or a handoff. */
  keysSeen: number;
  blockedAtExit: number;
  canaryUses: number;
}

/**
 * Whether an event's time is at or after `since`. Event times are UTC with a
 * Z (contracts/ledger-event.schema.json), so the seconds compare as text and
 * only a tie in the same second is parsed: this runs over a day of events
 * on every poll.
 */
function atOrAfter(ts: string, since: number, sinceText: string): boolean {
  const head = ts.slice(0, 19);
  if (head !== sinceText) return head > sinceText;
  return Date.parse(ts) >= since;
}

/** Counts over the events at or after `since` (a time in ms). */
export function evidenceSince(
  events: LedgerEvent[],
  since: number,
): EvidenceCounts {
  const counts: EvidenceCounts = {
    keysSeen: 0,
    blockedAtExit: 0,
    canaryUses: 0,
  };
  const sinceText = new Date(since).toISOString().slice(0, 19);
  for (const event of events) {
    if (!atOrAfter(event.ts, since, sinceText)) continue;
    if (event.action === "recorded" || event.action === "inherited")
      counts.keysSeen += 1;
    else if (event.action === "canary_used") counts.canaryUses += 1;
    else if (isBlockedAtExit(event)) counts.blockedAtExit += 1;
  }
  return counts;
}

export interface DayFigure {
  label: string;
  value: number;
  tone?: "ember";
}

export interface DayStrip {
  /** Still paging in today's history: the figures would only climb. */
  loading: boolean;
  /** History hit its cap: each figure is "at least". */
  lowerBound: boolean;
  /** The oldest event counted, when the figures are lower bounds. */
  from?: string;
  figures: DayFigure[];
}

/**
 * The "Last 24 hours" strip: only figures the event stream can give. Calls
 * checked, keys as references and keys in plain text have no source in the
 * API, so they are not shown.
 */
export function dayStrip(
  events: LedgerEvent[],
  history: EventHistory,
  now: Date,
): DayStrip {
  const counts = evidenceSince(events, now.valueOf() - DAY_MS);
  return {
    loading: !history.from && !history.complete,
    lowerBound: Boolean(history.from) && !history.complete,
    from: history.complete ? undefined : history.from,
    figures: [
      { label: "Keys seen", value: counts.keysSeen },
      { label: "Blocked at exit", value: counts.blockedAtExit },
      {
        label: "Canary uses",
        value: counts.canaryUses,
        tone: counts.canaryUses > 0 ? "ember" : undefined,
      },
    ],
  };
}

// ---------- The hero ----------

export interface HeroLink {
  href: string;
  label: string;
}

export interface HeroCopy {
  incident: boolean;
  eyebrow: string;
  title: string[];
  lede: string;
  open?: HeroLink;
  review?: HeroLink;
}

function otherLine(running: number): string {
  if (running === 0) return "No other agent was running.";
  if (running === 1) return "The other agent kept working.";
  return `The other ${running} kept working.`;
}

/** "rotated the two keys it owned", "flagged one shared key for its owner". */
function didParts(rotated: number, flagged: number, plural: boolean): string[] {
  const parts: string[] = [];
  if (rotated)
    parts.push(
      `rotated the ${countWord(rotated)} ${s(rotated, "key")} ${plural ? "they" : "it"} owned`,
    );
  if (flagged)
    parts.push(
      `flagged ${countWord(flagged)} shared ${s(flagged, "key")} for ${flagged === 1 ? "its owner" : "their owners"}`,
    );
  return parts;
}

function triggerSentence(agent: ConsoleAgent, trigger: BurnTrigger): string {
  switch (trigger.kind) {
    case "canary":
      return `The decoy key planted in ${agent.name}’s session was used from outside Burn${trigger.ip ? `, from ${trigger.ip}` : ""}.`;
    case "person":
      return trigger.reason
        ? sentence(`${trigger.who} burned ${agent.name}: ${trigger.reason}`)
        : `${trigger.who} burned ${agent.name} and gave no reason.`;
    case "reason":
      return sentence(`The reason given: ${trigger.reason}`);
    case "unknown":
      return "No reason was recorded for this burn.";
  }
}

/**
 * What started again after today's burns, from the replacements the API
 * linked: "worker-18 took over its task, and nothing else restarted."
 */
export function restarted(burned: ConsoleAgent[], model: ConsoleModel): string {
  const replaced = burned.filter((agent) => agent.instance?.replaced_by);
  if (!replaced.length) return "Nothing else restarted.";
  const names = replaced.map(
    (agent) => findAgent(model, agent.instance?.replaced_by)?.name,
  );
  const who =
    names.length <= 3 && names.every(Boolean)
      ? joinList(names as string[])
      : names.length === 1
        ? "A fresh agent"
        : `${capitalise(countWord(names.length))} fresh agents`;
  const task =
    burned.length === 1
      ? "its task"
      : replaced.length === 1
        ? `${replaced[0].name}’s task`
        : "their tasks";
  return `${who} took over ${task}, and nothing else restarted.`;
}

/**
 * The night hero's state: an incident when an agent was burned today
 * ("worker-07 was burned. The other 19 kept working."), all clear otherwise
 * ("All 20 agents are clean."). `burned` is today's burns, newest first;
 * `incidents` says what each burn did.
 */
export function heroCopy({
  model,
  events,
  burned,
  incidents,
  flags,
  history,
  region,
}: {
  model: ConsoleModel;
  events: LedgerEvent[];
  burned: ConsoleAgent[];
  incidents: Incident[];
  flags: Flag[];
  history: EventHistory;
  region?: string;
}): HeroCopy {
  const running = model.counts.running;
  const newest = burned[0];
  if (newest) {
    const today = new Map(
      incidents
        .filter((incident) => incident.agent.burnedToday)
        .map((incident) => [incident.agent.key, incident]),
    );
    const own = today.get(newest.key);
    const time =
      newest.burnedAtFrom === "last-seen" ? "" : shortTime(newest.burnedAt);
    const eyebrow = `Incident ${String(burned.length).padStart(2, "0")}${time ? ` / ${time}` : ""}`;
    // The key to review: one still waiting on its owner from any of today's
    // burns (newest burn first), else one the newest burn flagged.
    const openKey =
      burned.map((agent) => today.get(agent.key)?.openKeys[0]).find(Boolean) ??
      own?.flagged[0];
    const links = {
      open:
        burned.length === 1
          ? { href: agentHref(newest.key), label: "Open the incident" }
          : { href: "#/incidents", label: "Open the incidents" },
      review: openKey
        ? { href: "#/credentials", label: `Review ${keyName(openKey)}` }
        : undefined,
    };
    if (burned.length === 1) {
      const did = didParts(
        own?.rotated.length ?? 0,
        own?.flagged.length ?? 0,
        false,
      );
      const lede = [
        triggerSentence(newest, burnTrigger(newest, events)),
        `Burn ${joinList([`revoked ${newest.name}`, ...did])}.`,
        restarted(burned, model),
      ].join(" ");
      return {
        incident: true,
        eyebrow,
        title: [`${newest.name} was burned.`, otherLine(running)],
        lede,
        ...links,
      };
    }
    const rotated = new Set<string>();
    const flagged = new Set<string>();
    for (const agent of burned) {
      for (const ref of today.get(agent.key)?.rotated ?? []) rotated.add(ref);
      for (const ref of today.get(agent.key)?.flagged ?? []) flagged.add(ref);
    }
    const did = didParts(rotated.size, flagged.size, true);
    return {
      incident: true,
      eyebrow,
      title: [
        `${burned.length} agents were burned today.`,
        otherLine(running).replace("The other agent", "One other agent"),
      ],
      lede: `Burn ${joinList(["revoked each of them", ...did])}. ${restarted(burned, model)}`,
      ...links,
    };
  }

  const title =
    running === 0
      ? "No agents are running."
      : running === 1
        ? "The one agent running is clean."
        : `All ${running} agents are clean.`;
  return {
    incident: false,
    eyebrow: region ? `Live / ${region}` : "Live",
    title: [title],
    lede:
      running === 0
        ? "Agents appear here as they join. Add agents to start a role."
        : allClearLede(events, flags, history, model.now),
  };
}

/** What today held, in one sentence, or that it held nothing. */
function allClearLede(
  events: LedgerEvent[],
  flags: Flag[],
  history: EventHistory,
  now: Date,
): string {
  const counts = evidenceSince(events, startOfDay(now).valueOf());
  const open = flags.filter((flag) => flag.state === "open").length;
  const partial = Boolean(history.from) && !history.complete;
  const since = partial ? `since ${dayTime(history.from, now)}` : "today";
  const least = partial ? "at least " : "";
  const parts: string[] = [];
  if (counts.canaryUses)
    parts.push(
      `${least}${countWord(counts.canaryUses)} canary ${s(counts.canaryUses, "use")}`,
    );
  if (counts.blockedAtExit)
    parts.push(
      `${least}${countWord(counts.blockedAtExit)} ${s(counts.blockedAtExit, "leak")} blocked at exit`,
    );
  if (open)
    parts.push(
      `${countWord(open)} flagged ${s(open, "key")} waiting on ${open === 1 ? "its owner" : "their owners"}`,
    );
  if (!parts.length)
    return partial
      ? `No canary uses or blocked leaks ${since}, and no open flags.`
      : "No canary uses, blocked leaks or open flags today.";
  return `${capitalise(since)}: ${joinList(parts)}. No agent was burned.`;
}

// ---------- Running now ----------

export interface NowSquare {
  key: string;
  state: "running" | "burned";
  label: string;
  href: string;
}

export interface NowRow {
  name: string;
  href: string;
  /** The role's one line: its owner, or where it is managed. */
  what: string;
  squares: NowSquare[];
  running: number;
  burnedToday: number;
  /** Changes only when something on the row would change. */
  signature: string;
}

const MANAGED: Record<Role["managed_by"], string> = {
  code: "Defined in code",
  ui: "Made in the dashboard",
  scenario: "Scenario agent",
};

/** The role's one line. The API has no description, so its owner. */
export function roleLine(group: RoleGroup): string {
  const role = group.role;
  if (role?.owner) return role.owner;
  const owner = group.agents.find((agent) => agent.owner)?.owner;
  if (owner) return owner;
  return role ? MANAGED[role.managed_by] : "";
}

function squareLabel(agent: ConsoleAgent): string {
  if (agent.state === "running") return `${agent.name}, running`;
  const time =
    agent.burnedAtFrom === "last-seen" ? "" : shortTime(agent.burnedAt);
  return time
    ? `${agent.name}, burned at ${time}`
    : `${agent.name}, burned today`;
}

/**
 * One row per role with an agent running now or burned today, largest
 * first. A square per running agent and per agent burned today, in number
 * order. Finished agents and older burns are left to the role page.
 */
export function runningNowRows(model: ConsoleModel): NowRow[] {
  const rows: NowRow[] = [];
  for (const group of model.roles) {
    if (!group.running.length && !group.burnedToday.length) continue;
    const squares: NowSquare[] = [];
    for (const agent of group.agents) {
      if (agent.state !== "running" && !agent.burnedToday) continue;
      squares.push({
        key: agent.key,
        state: agent.state === "running" ? "running" : "burned",
        label: squareLabel(agent),
        href: agentHref(agent.key),
      });
    }
    const what = roleLine(group);
    rows.push({
      name: group.name,
      href: roleHref(group.name),
      what,
      squares,
      running: group.running.length,
      burnedToday: group.burnedToday.length,
      signature: [
        what,
        ...squares.map((square) => `${square.key}|${square.label}`),
      ].join("\n"),
    });
  }
  return rows;
}

/** Whether a row can skip re-rendering: nothing on it changed. */
export function sameRow(a: NowRow, b: NowRow): boolean {
  return a.name === b.name && a.signature === b.signature;
}

// ---------- Needs a decision ----------

const LEVEL_RANK: Record<FlagLevel, number> = {
  reported: 0,
  attempted: 1,
  seen: 2,
};

const LEVEL_VERB: Record<FlagLevel, string> = {
  seen: "saw it",
  attempted: "tried to move it",
  reported: "was burned holding it",
};

const LEVEL_ADVICE: Record<FlagLevel, string> = {
  reported: "Treat it as leaked.",
  attempted: "Burn stopped it leaving; its owner decides whether to rotate it.",
  seen: "Nothing tried to move it, and only its owner can rotate it.",
};

/** A body with identifiers set apart, so they can be set in mono. */
export type Text = (string | { id: string })[];

interface DecisionBase {
  key: string;
  tag: string;
  /** Ember ink: evidence that something left or was used. */
  hot: boolean;
  title: string;
  /** Whether the title is an identifier, set in mono. */
  titleId: boolean;
  body: Text;
}

export type Decision =
  | (DecisionBase & { kind: "flag"; flag: Flag })
  | (DecisionBase & { kind: "canary"; href: string; name: string })
  | (DecisionBase & { kind: "caller"; newest: string });

function flagDecision(flag: Flag, model: ConsoleModel): Decision {
  const level = flag.flag_level;
  const evidence = flag.evidence?.[level];
  const who =
    (evidence &&
      agentForEvent(model, {
        agent_id: evidence.agent_id ?? "",
        session_id: evidence.session_id ?? null,
      })?.name) ||
    "An agent";
  const tool = evidence?.tool ? ` through ${evidence.tool}` : "";
  const at = evidence?.ts ? ` at ${dayTime(evidence.ts, model.now)}` : "";
  // A burn flags the shared keys its agent saw: say the agent was burned.
  const verb =
    level === "seen" && evidence?.source === "burn"
      ? "saw it before it was burned"
      : LEVEL_VERB[level];
  const others =
    flag.instance_count > 1
      ? ` ${capitalise(countWord(flag.instance_count))} agents have seen it.`
      : "";
  const owner = flag.owner_agent_id;
  const tag =
    !owner || owner === "shared"
      ? "Shared key"
      : `${findAgent(model, owner)?.name ?? "Another agent"}’s key`;
  return {
    kind: "flag",
    key: `flag:${flag.secret_ref}`,
    flag,
    tag: `${tag} / ${level}`,
    hot: level !== "seen",
    title: keyName(flag.secret_ref),
    titleId: true,
    body: [`${who} ${verb}${tool}${at}.${others} ${LEVEL_ADVICE[level]}`],
  };
}

function timeSpan(first: string, last: string): string {
  const a = shortTime(first);
  const b = shortTime(last);
  return a === b ? `at ${a}` : `between ${a} and ${b}`;
}

interface CallerGroup {
  key: string;
  client?: string;
  egress: boolean;
  count: number;
  first: string;
  last: string;
  newest: string;
}

/**
 * Today's refused calls from callers Burn never enrolled, one group per
 * badge client (the id the gateway names), and one for requests that
 * reached the egress proxy with no session. `events` is newest first.
 */
export function unknownCallers(
  events: LedgerEvent[],
  now: Date,
): CallerGroup[] {
  const groups = new Map<string, CallerGroup>();
  for (const event of events) {
    if (!isUnknownCaller(event) || !isSameDay(event.ts, now)) continue;
    const client = event.detail?.match(/^unknown badge client (\S+)/)?.[1];
    const egress = event.source === "egress";
    const key = client
      ? `badge:${client}`
      : egress
        ? "egress"
        : "badge:unknown";
    const group = groups.get(key);
    if (!group)
      groups.set(key, {
        key,
        client,
        egress,
        count: 1,
        first: event.ts,
        last: event.ts,
        newest: event.event_id,
      });
    else {
      group.count += 1;
      if (event.ts < group.first) group.first = event.ts;
      if (event.ts > group.last) group.last = event.ts;
      if (event.event_id > group.newest) group.newest = event.event_id;
    }
  }
  return [...groups.values()].sort(
    (a, b) => b.last.localeCompare(a.last) || b.newest.localeCompare(a.newest),
  );
}

function callerDecision(group: CallerGroup): Decision {
  const n = group.count;
  const all = n === 1 ? "It was refused" : `All ${countWord(n)} were refused`;
  const body: Text = group.egress
    ? [
        `${capitalise(countWord(n))} ${s(n, "request")} reached the egress proxy ${timeSpan(group.first, group.last)} with no session Burn issued. ${all}.`,
      ]
    : group.client
      ? [
          `${capitalise(countWord(n))} tool ${s(n, "call")} from badge client `,
          { id: group.client },
          ` ${timeSpan(group.first, group.last)}. ${all} before any tool ran.`,
        ]
      : [
          `${capitalise(countWord(n))} tool ${s(n, "call")} ${timeSpan(group.first, group.last)} carried a badge Burn never issued. ${all} before any tool ran.`,
        ];
  return {
    kind: "caller",
    key: `caller:${group.key}`,
    newest: group.newest,
    tag: "Unknown caller",
    hot: false,
    title: group.egress
      ? "A caller with no Burn session"
      : "A badge Burn did not issue",
    titleId: false,
    body,
  };
}

/**
 * Canary uses today on agents that are still running: their role reports
 * canary uses without burning (auto_burn off), or the burn has not landed.
 */
function canaryDecisions(
  events: LedgerEvent[],
  model: ConsoleModel,
): Decision[] {
  const seen = new Set<string>();
  const list: Decision[] = [];
  for (const event of events) {
    if (event.action !== "canary_used" || !isSameDay(event.ts, model.now))
      continue;
    const agent = agentForEvent(model, event);
    if (!agent || agent.state !== "running" || seen.has(agent.key)) continue;
    seen.add(agent.key);
    const ip = canaryIp(event);
    const reportOnly =
      model.roleByName.get(agent.role)?.role?.auto_burn === false;
    list.push({
      kind: "canary",
      key: `canary:${agent.key}`,
      href: agentHref(agent.key),
      name: agent.name,
      tag: "Canary used / not burned",
      hot: true,
      title: agent.name,
      titleId: true,
      body: [
        `Its decoy key was used outside Burn${ip ? ` from ${ip}` : ""} at ${shortTime(event.ts)}. ${
          reportOnly
            ? `The ${agent.role} role reports canary uses without burning, so this is yours to decide.`
            : "Burn has not burned it yet."
        }`,
      ],
    });
  }
  return list;
}

/** Callers hidden in this browser, by group key, with the newest event then. */
export type Ignored = Record<string, string>;

/**
 * Everything waiting on a person, most serious first: canary uses on agents
 * still running, open flags (reported, attempted, seen), then unknown
 * callers not ignored since their last refusal.
 */
export function decisions({
  flags,
  events,
  model,
  ignored,
}: {
  flags: Flag[];
  events: LedgerEvent[];
  model: ConsoleModel;
  ignored: Ignored;
}): Decision[] {
  const open = flags
    .filter((flag) => flag.state === "open")
    .sort(
      (a, b) =>
        LEVEL_RANK[a.flag_level] - LEVEL_RANK[b.flag_level] ||
        (b.updated_at ?? b.flagged_at ?? "").localeCompare(
          a.updated_at ?? a.flagged_at ?? "",
        ) ||
        a.secret_ref.localeCompare(b.secret_ref),
    )
    .map((flag) => flagDecision(flag, model));
  const callers = unknownCallers(events, model.now)
    .map(callerDecision)
    .filter(
      (item) =>
        item.kind !== "caller" ||
        !ignored[item.key] ||
        item.newest > ignored[item.key],
    );
  return [...canaryDecisions(events, model), ...open, ...callers];
}

// ---------- Burned today ----------

export interface BurnedRow {
  key: string;
  name: string;
  href: string;
  why: string;
  pill: string;
  pillTone: "neutral" | "flag";
}

/**
 * Today's burns, newest first, with why each happened and what is left:
 * a shared key still open, a replacement that needs a person, a rotation
 * that failed, or a replacement on its way. Never "Contained".
 */
export function burnedRows(
  burned: ConsoleAgent[],
  incidents: Incident[],
  events: LedgerEvent[],
): BurnedRow[] {
  const byKey = new Map(
    incidents.map((incident) => [incident.agent.key, incident]),
  );
  return burned.map((agent) => {
    const incident = byKey.get(agent.key);
    const time =
      agent.burnedAtFrom === "last-seen" ? "" : shortTime(agent.burnedAt);
    const why = `${agent.role}${time ? `, ${time}` : ""}. ${triggerShort(burnTrigger(agent, events))}`;
    const replacement = agent.instance?.replacement?.status;
    let pill = "Burned";
    let pillTone: BurnedRow["pillTone"] = "neutral";
    if (incident?.status === "keys-open") {
      const n = incident.openKeys.length;
      [pill, pillTone] = [`${n} ${s(n, "key")} open`, "flag"];
    } else if (incident?.status === "needs-person")
      [pill, pillTone] = ["Needs a person", "flag"];
    else if (incident?.status === "rotation-failed")
      [pill, pillTone] = ["Rotation failed", "flag"];
    else if (replacement === "linked") pill = "Replaced";
    else if (replacement === "pending") pill = "Replacing";
    return {
      key: agent.key,
      name: agent.name,
      href: agentHref(agent.key),
      why,
      pill,
      pillTone,
    };
  });
}

// ---------- Live ----------

/** How many events the Live card shows. */
export const LIVE_ROWS = 8;
