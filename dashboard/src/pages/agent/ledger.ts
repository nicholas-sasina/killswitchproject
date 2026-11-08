/**
 * The exposure ledger as the agent page shows it: one row per key the agent
 * saw, in the website hero panel's layout (credential and source, evidence,
 * action). Everything comes from GET /agents/{id}/ledger plus the agent's
 * own events; nothing is guessed.
 *
 *   evidence  "Plain text", "Seen as a reference", "Plain text; leak blocked
 *             at exit" (a blocked event names the key), "Old value refused"
 *             (a rejected event names it after rotation)
 *   action    Tracked, Flagged (Owner review once burned), Rotated, and for
 *             the canary Watching, Used or Revoked
 *   dot       ember for evidence, ink for its own keys once burned, faint
 *             otherwise
 */
import type { LedgerEntry, LedgerEvent } from "../../contracts";
import {
  inPlainText,
  keyKind,
  keyLabel,
  type KeyKind,
} from "../../features/burn/steps";
import { canaryIp } from "../../model/burns";
import { keyName } from "../../model/keys";

export type Tone = "neutral" | "flag" | "burn" | "done";

export interface LedgerRow {
  id: string;
  kind: KeyKind;
  /** "session canary", "crm_db_url", or "Unrecognised key". */
  name: string;
  /** Whether the name is an identifier (set in mono). */
  mono: boolean;
  /** Where it came from: "its own copy", "read_config, shared". */
  source: string;
  evidence: string;
  /** Evidence set in ink rather than the quiet colour. */
  hot: boolean;
  dot: "ember" | "ink" | "faint";
  action: { text: string; tone: Tone };
}

export interface LedgerCounts {
  /** Every key it saw, its canary included. */
  seen: number;
  /** Keys other than its canary whose plain text reached it. */
  plain: number;
  /** Its own keys (its canary aside). */
  owned: number;
}

export function ledgerCounts(
  entries: LedgerEntry[],
  agentId: string,
): LedgerCounts {
  let plain = 0;
  let owned = 0;
  for (const entry of entries) {
    const kind = keyKind(entry, agentId);
    if (kind === "canary") continue;
    if (inPlainText(entry)) plain += 1;
    if (kind === "owned") owned += 1;
  }
  return { seen: entries.length, plain, owned };
}

/** What it read a key from: "read_ticket T-1001", or the tool alone. */
function place(entry: LedgerEntry): string {
  const ref = entry.source_ref ?? "";
  const split = ref.indexOf(":");
  if (split > 0) {
    const [tool, resource] = [ref.slice(0, split), ref.slice(split + 1)];
    return resource && !resource.startsWith("sha256:")
      ? `${tool} ${resource}`
      : tool;
  }
  return entry.via || ref || "a tool";
}

function sourceLine(
  entry: LedgerEntry,
  kind: KeyKind,
  nameOf: (agentId: string) => string | undefined,
): string {
  if (kind === "canary") {
    const ref = entry.source_ref ?? "";
    const resource = ref.includes(":") ? ref.slice(ref.indexOf(":") + 1) : "";
    return resource && !resource.startsWith("sha256:")
      ? `its decoy key, planted in ${resource}`
      : "its decoy key";
  }
  if (kind === "owned") return "its own copy";
  const from =
    entry.source === "handoff" && entry.via
      ? `handed over by ${nameOf(entry.via) ?? entry.via}`
      : place(entry);
  if (kind === "unknown") return `${from}, owner unknown`;
  if (entry.owner === "agent" && entry.owner_agent_id)
    return `${from}, ${nameOf(entry.owner_agent_id) ?? entry.owner_agent_id}'s own key`;
  return `${from}, shared`;
}

const KIND_ORDER: Record<KeyKind, number> = {
  canary: 0,
  shared: 1,
  owned: 2,
  unknown: 3,
};

/**
 * The ledger's rows: its canary first, then the keys that are evidence,
 * then its own keys, then the rest, each group oldest first.
 */
export function ledgerRows(
  entries: LedgerEntry[],
  {
    agentId,
    events,
    burned,
    nameOf = () => undefined,
  }: {
    agentId: string;
    /** This agent's events. */
    events: LedgerEvent[];
    burned: boolean;
    nameOf?: (agentId: string) => string | undefined;
  },
): LedgerRow[] {
  const blocked = new Map<string, LedgerEvent>();
  const rejected = new Set<string>();
  let canaryUse: LedgerEvent | undefined;
  for (const event of events) {
    if (event.action === "blocked" && event.secret_ref)
      blocked.set(event.secret_ref, event);
    else if (event.action === "rejected" && event.secret_ref)
      rejected.add(event.secret_ref);
    else if (event.action === "canary_used") canaryUse ??= event;
  }
  const rows = entries.map((entry, index) => {
    const ref = entry.secret_ref;
    const kind = keyKind(entry, agentId);
    const id = ref ?? `${entry.fingerprint}:${index}`;
    const base = {
      id,
      kind,
      name:
        kind === "canary"
          ? "session canary"
          : ref
            ? keyLabel(ref, agentId)
            : "Unrecognised key",
      mono: Boolean(ref),
      source: ref
        ? sourceLine(entry, kind, nameOf)
        : `fingerprint ${entry.fingerprint.slice(0, 8)}`,
    };
    if (kind === "canary") {
      const used =
        canaryUse && (!canaryUse.secret_ref || canaryUse.secret_ref === ref)
          ? canaryUse
          : undefined;
      const ip = canaryIp(used);
      return {
        ...base,
        evidence: used
          ? `Used outside Burn${ip ? ` from ${ip}` : ""}`
          : "Exposed, never used",
        hot: Boolean(used),
        dot: "ember" as const,
        action: burned
          ? { text: "Revoked", tone: "done" as const }
          : used
            ? { text: "Used", tone: "burn" as const }
            : { text: "Watching", tone: "neutral" as const },
        first: entry.first_seen,
      };
    }
    const plain = inPlainText(entry);
    const block = ref ? blocked.get(ref) : undefined;
    const refused = ref ? rejected.has(ref) : false;
    const rotated = entry.status === "rotated";
    const aws = entry.secret_type === "aws_key" || /aws/i.test(keyName(ref));
    const where = block?.source === "egress" ? "at exit" : "in a handoff";
    const evidence = rotated
      ? refused
        ? "Old value refused"
        : aws
          ? "Old key inactive"
          : "Old value no longer works"
      : block
        ? `${plain ? "Plain text; leak" : "Leak"} blocked ${where}`
        : plain
          ? "Plain text"
          : "Seen as a reference";
    const evidential = (kind !== "owned" && plain) || Boolean(block);
    return {
      ...base,
      evidence,
      hot: evidential || refused,
      dot: evidential
        ? ("ember" as const)
        : kind === "owned" && (burned || rotated)
          ? ("ink" as const)
          : ("faint" as const),
      action: rotated
        ? { text: "Rotated", tone: "done" as const }
        : entry.status === "flagged"
          ? { text: burned ? "Owner review" : "Flagged", tone: "flag" as const }
          : { text: "Tracked", tone: "neutral" as const },
      first: entry.first_seen,
    };
  });
  const group = (row: (typeof rows)[number]) =>
    row.kind === "shared" && row.dot !== "ember"
      ? 2.5 // shared keys seen only as references sit after its own
      : KIND_ORDER[row.kind];
  return rows
    .sort(
      (a, b) =>
        group(a) - group(b) || (a.first ?? "").localeCompare(b.first ?? ""),
    )
    .map(({ first: _first, ...row }) => row);
}

/**
 * Keys whose plain text reaching it is evidence: anything it saw in plain
 * text but its own keys and its canary. Reading one is marked on its
 * timeline as the ledger marks the key.
 */
export function evidenceRefs(
  entries: readonly LedgerEntry[] | undefined,
  agentId: string,
): Set<string> {
  const refs = new Set<string>();
  for (const entry of entries ?? []) {
    const kind = keyKind(entry, agentId);
    if (
      entry.secret_ref &&
      kind !== "owned" &&
      kind !== "canary" &&
      inPlainText(entry)
    )
      refs.add(entry.secret_ref);
  }
  return refs;
}
