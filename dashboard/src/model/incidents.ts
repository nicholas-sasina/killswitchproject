/**
 * Incidents: burned agents and whether each still needs someone. The
 * navigation's Incidents count and the Incidents page's Open filter both
 * come from here, so they always agree.
 *
 * An incident is open while a shared key its burn flagged is still waiting
 * on its owner, while its replacement needs a person, or while a rotation
 * the burn tried has not been finished.
 */
import type { Flag, LedgerEvent } from "../contracts";
import type { ConsoleAgent, ConsoleModel } from "./console";

export type IncidentStatus =
  "keys-open" | "needs-person" | "rotation-failed" | "burned";

export interface Incident {
  agent: ConsoleAgent;
  status: IncidentStatus;
  open: boolean;
  /** Shared keys this burn flagged that are still open, by reference. */
  openKeys: string[];
  /** Keys this burn rotated, by reference. */
  rotated: string[];
  /** Keys this burn flagged, by reference. */
  flagged: string[];
}

/** The open flags, by reference. */
export function openFlagRefs(flags: Flag[] | undefined): Set<string> {
  return new Set(
    (flags ?? [])
      .filter((flag) => flag.state === "open")
      .map((flag) => flag.secret_ref),
  );
}

/**
 * What one burn did, from the burn's own events: Burn writes a `rotated`
 * event for each key the agent owned and a `flagged` event for each shared
 * key it saw, on the burned agent's id. For an agent id with several
 * sessions, only events after this burn's own revocation count.
 */
export function incidentFor(
  agent: ConsoleAgent,
  events: LedgerEvent[],
  open: Set<string>,
): Incident {
  const after = agent.burnedAtFrom === "event" ? agent.burnedAt : undefined;
  const rotated: string[] = [];
  const flagged: string[] = [];
  const failed = new Set<string>();
  // Oldest first, so a later rotation clears an earlier failure.
  const own = events
    .filter(
      (event) =>
        event.agent_id === agent.agentId &&
        event.source === "burn" &&
        event.secret_ref &&
        (!after || event.ts >= after),
    )
    .sort((a, b) => a.event_id.localeCompare(b.event_id));
  for (const event of own) {
    const ref = event.secret_ref as string;
    if (event.action === "rotated") {
      rotated.push(ref);
      failed.delete(ref);
    } else if (event.action === "flagged") {
      if (/^rotation (failed|deferred)/.test(event.detail ?? ""))
        failed.add(ref);
      else flagged.push(ref);
    }
  }
  const openKeys = [...new Set(flagged)].filter((ref) => open.has(ref));
  const status: IncidentStatus = openKeys.length
    ? "keys-open"
    : agent.instance?.replacement?.status === "needs_person"
      ? "needs-person"
      : failed.size
        ? "rotation-failed"
        : "burned";
  return {
    agent,
    status,
    open: status !== "burned",
    openKeys,
    rotated: [...new Set(rotated)],
    flagged: [...new Set(flagged)],
  };
}

/** Every burned agent as an incident, newest first. */
export function incidents(
  model: ConsoleModel,
  events: LedgerEvent[],
  flags: Flag[] | undefined,
): Incident[] {
  const open = openFlagRefs(flags);
  return model.agents
    .filter((agent) => agent.state === "burned")
    .map((agent) => incidentFor(agent, events, open))
    .sort(
      (a, b) =>
        (b.agent.burnedAt ?? "").localeCompare(a.agent.burnedAt ?? "") ||
        a.agent.name.localeCompare(b.agent.name),
    );
}

export interface IncidentCounts {
  all: number;
  open: number;
  closed: number;
  /** Open flags: the keys waiting on their owners. */
  keysWaiting: number;
}

export function incidentCounts(
  list: Incident[],
  flags: Flag[] | undefined,
): IncidentCounts {
  const open = list.filter((incident) => incident.open).length;
  return {
    all: list.length,
    open,
    closed: list.length - open,
    keysWaiting: openFlagRefs(flags).size,
  };
}
