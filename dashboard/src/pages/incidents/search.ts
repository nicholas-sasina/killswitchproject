/**
 * Searching the burned agents: every word of the query must appear in what
 * the row says (its name, role, keys, why it was burned and its status),
 * ignoring case. Counts under a search come from the same matched rows, so
 * the tabs, the card's count and the list always agree.
 */
import { keyName, type Incident } from "../../model";

export interface Searchable {
  incident: Incident;
  /** The "Why it was burned" line: trigger, reason, person. */
  why: string;
  /** The status pill's words. */
  status: string;
}

/** The query's words, lower-cased; none for a blank query. */
export function searchTerms(query: string | undefined): string[] {
  return (query ?? "").toLowerCase().split(/\s+/).filter(Boolean);
}

/** Everything a row can be found by, as one lower-case string. */
export function searchText({ incident, why, status }: Searchable): string {
  const { agent } = incident;
  const keys = [
    ...new Set([
      ...incident.rotated,
      ...incident.flagged,
      ...incident.openKeys,
    ]),
  ].flatMap((ref) => [ref, keyName(ref)]);
  return [
    agent.name,
    agent.role,
    agent.agentId,
    agent.sessionId ?? "",
    ...keys,
    why,
    status,
  ]
    .join("\n")
    .toLowerCase();
}

/** Whether every term appears in the row's text. */
export function matches(text: string, terms: string[]): boolean {
  return terms.every((term) => text.includes(term));
}

export type IncidentFilter = "all" | "open" | "burned";

/** The tabs' counts, from the rows the search kept. */
export function tabCounts(
  rows: { incident: Incident }[],
): Record<IncidentFilter, number> {
  const open = rows.filter((row) => row.incident.open).length;
  return { all: rows.length, open, burned: rows.length - open };
}

export function inTab(incident: Incident, filter: IncidentFilter): boolean {
  return filter === "all" || (filter === "open") === incident.open;
}
