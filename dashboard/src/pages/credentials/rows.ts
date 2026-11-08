/**
 * The Credentials page's rows: every flagged key, open ones first.
 *
 * GET /flags lists open and dismissed flags; once an owner rotates a key
 * the new version has no flag, so the list drops it. A rotated key is
 * still a decision worth seeing, so it comes from the `rotated` event Burn
 * wrote for it (in the loaded events).
 */
import type { Flag, FlagLevel, LedgerEvent } from "../../contracts";
import {
  flagKey,
  levelRank,
  ownerRotations,
  reopenNote,
  type OwnerRotation,
} from "../../features/flags/describe";
import { dayTime, personLabel } from "../../model";

export type CredentialFilter = "open" | "resolved" | "all";

export interface CredentialRow {
  ref: string;
  key: string;
  state: "open" | "dismissed" | "rotated";
  /** The level reached, when known. */
  level?: FlagLevel;
  flag?: Flag;
  rotation?: OwnerRotation;
}

function decidedAt(row: CredentialRow): string {
  return row.flag?.resolution?.at ?? row.rotation?.at ?? "";
}

/** Open first (highest level first), then decisions, newest first. */
export function credentialRows(
  flags: Flag[],
  events: LedgerEvent[],
): CredentialRow[] {
  const listed = new Set(flags.map((flag) => flag.secret_ref));
  const open: CredentialRow[] = [];
  const resolved: CredentialRow[] = [];
  for (const flag of flags) {
    const row: CredentialRow = {
      ref: flag.secret_ref,
      key: flagKey(flag),
      state: flag.state,
      level: flag.flag_level,
      flag,
    };
    (flag.state === "open" ? open : resolved).push(row);
  }
  for (const rotation of ownerRotations(events)) {
    if (listed.has(rotation.ref)) continue;
    resolved.push({
      ref: rotation.ref,
      key: flagKey({ secret_ref: rotation.ref }),
      state: "rotated",
      level: rotation.level,
      rotation,
    });
  }
  open.sort(
    (a, b) =>
      levelRank(b.level) - levelRank(a.level) ||
      (a.flag?.flagged_at ?? "").localeCompare(b.flag?.flagged_at ?? ""),
  );
  resolved.sort((a, b) => decidedAt(b).localeCompare(decidedAt(a)));
  return [...open, ...resolved];
}

export function credentialCounts(
  rows: CredentialRow[],
): Record<CredentialFilter, number> {
  const open = rows.filter((row) => row.state === "open").length;
  return { open, resolved: rows.length - open, all: rows.length };
}

export function inFilter(row: CredentialRow, filter: CredentialFilter) {
  return filter === "all" || (filter === "open") === (row.state === "open");
}

/** A decided row's outcome: who decided, why, when, and what reopens it. */
export function decisionLines(
  row: CredentialRow,
  now: Date,
): { title: string; detail: string; note?: string } | undefined {
  if (row.state === "open") return undefined;
  const resolution = row.flag?.resolution;
  const who = personLabel(resolution?.by ?? row.rotation?.actor);
  const reason = resolution?.reason ?? row.rotation?.reason;
  const at = dayTime(decidedAt(row), now);
  const detail = [reason ? `“${reason}”` : "", at].filter(Boolean).join(", ");
  if (row.state === "rotated")
    return {
      title: `Rotated by ${who || "its owner"}`,
      detail,
    };
  return {
    title: `Dismissed by ${who || "its owner"}`,
    detail,
    note: reopenNote(resolution?.level ?? row.level ?? "seen"),
  };
}
