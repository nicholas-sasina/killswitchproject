/**
 * Which ledger events are alerts, how serious each one is, and what an
 * operator should understand from it.
 *
 * Derived entirely from the event stream the dashboard already polls, so the
 * alerts view costs no extra requests.
 *
 * Severity is assigned by what the event actually shows, not by how alarming
 * it sounds. A canary credential being presented is proof that a credential left
 * and was used by someone, so it outranks a blocked egress attempt, which is
 * proof the exit check did its job.
 */
import type { LedgerEvent } from "./contracts";

export type Tier = "crit" | "high" | "warn" | "info";

export const TIERS: Tier[] = ["crit", "high", "warn", "info"];

/** One name per severity, used in the list, the tallies and the detail. */
export const TIER_NAME: Record<Tier, string> = {
  crit: "Critical",
  high: "High",
  warn: "Review",
  info: "Noted",
};

/** A plain sentence for each severity, shown beside the tallies. */
export const TIER_MEANING: Record<Tier, string> = {
  crit: "Evidence a credential was used by someone else",
  high: "Stopped at the boundary, or a dead credential reused",
  warn: "Contained inside the system; worth a look",
  info: "Recorded for the audit trail; no action needed",
};

/** Ordering for "most serious first". */
const TIER_RANK: Record<Tier, number> = { crit: 0, high: 1, warn: 2, info: 3 };

export interface Alert {
  event: LedgerEvent;
  tier: Tier;
  title: string;
  /** What the event means. */
  why: string;
  /** What the operator should do about it. */
  next: string;
}

interface Rule {
  tier: Tier;
  title: string;
  why: string;
  next: string;
}

function classify(event: LedgerEvent): Rule | undefined {
  switch (event.action) {
    case "canary_used":
      return {
        tier: "crit",
        title: "Canary credential presented",
        why: "A credential that exists only to be stolen was presented to the provider. Nothing legitimate ever uses it, so this is evidence that a credential left this agent and was used by someone else.",
        next: "Burn the agent if it is still active, then read its exposure ledger to see every other credential it held or saw. Those are the next candidates for rotation.",
      };
    case "unregistered":
      return {
        tier: "high",
        title: "Unregistered agent refused",
        why: "Something presented itself at the gateway without a badge Burn issued. Either an instance outlived its session, or a process is reaching the gateway that was never enrolled.",
        next: "Match the agent id against current instances. If none matches, treat the host as untrusted and revoke the join token it used.",
      };
    case "rejected":
      return {
        tier: "high",
        title: "Rotated credential replayed",
        why: "A credential that Burn already rotated was presented to the provider and refused. The value was held somewhere outside Burn at the moment it was rotated.",
        next: "Confirm the refusal came from the provider and not from a stale client of your own. If it did not, find what is still holding the old value.",
      };
    case "blocked":
      // Where the block happened changes what it proves, so the two are
      // separate alerts rather than one with a vague title.
      return event.source === "egress"
        ? {
            tier: "high",
            title: "Credential blocked leaving the network",
            why: "The egress proxy recognised a live credential in an outbound request and stopped it. The agent tried to send a secret to somewhere outside the system.",
            next: "The credential did not leave, so there is no rotation to do yet. Check whether the destination was expected; a novel transformation could slip past, and the allowlist is the backstop.",
          }
        : {
            tier: "warn",
            title: "Credential blocked in a handoff",
            why: "One agent tried to pass a credential to another through a tool call and the gateway removed it. Contained inside the system, so nothing reached the network.",
            next: "Usually the agent copying a config file verbatim. Worth noting if one agent does it repeatedly, which points at its instructions.",
          };
    case "refused":
      return {
        tier: "warn",
        title: "Burned agent's call refused",
        why: "An agent whose badge was revoked tried to call a tool and was turned away. This is containment working, visible after the fact.",
        next: "None, unless the agent keeps trying long after the burn, which means its process did not stop.",
      };
    case "flagged": {
      // flag_level says how far the credential actually travelled.
      const reported = event.flag_level === "reported";
      const attempted = event.flag_level === "attempted";
      if (reported || attempted)
        return {
          tier: "warn",
          title: reported
            ? "Shared credential reported for review"
            : "Shared credential flagged after an attempt",
          why: `A credential owned by someone else was ${attempted ? "involved in an attempt to move it out" : "reported by its owner"}. Burn cannot rotate what it does not own, so it is flagged instead.`,
          next: "Pass the reference to whoever owns the credential. Only they can rotate it.",
        };
      return {
        tier: "info",
        title: "Shared credential seen",
        why: "An agent saw a credential belonging to someone else. Seen only; no attempt was made to move it.",
        next: "No action. It is recorded so the owner can judge whether the exposure matters.",
      };
    }
    case "revoked":
      return {
        tier: "info",
        title: "Agent badge revoked",
        why: "A burn completed and the agent's badge no longer authenticates.",
        next: "Confirm the rotation and flag counts on the containment result.",
      };
    case "dismissed":
      return {
        tier: "info",
        title: "Flag dismissed by owner",
        why: "The credential's owner reviewed a flag and judged it not to matter.",
        next: "None. Recorded so the decision has an audit trail.",
      };
    default:
      // recorded, inherited, rotated, issued, started, exited: normal
      // bookkeeping, and not alerts.
      //
      // Also the landing place for an action added to the contract that this
      // build has not mirrored yet. Treating it as "not an alert" is the
      // deliberate choice: inventing a severity for an unknown event would
      // be worse than leaving it in the live stream, where it still shows.
      return undefined;
  }
}

/** The subset of the stream that needs an operator's attention. */
export function toAlerts(events: LedgerEvent[]): Alert[] {
  const alerts: Alert[] = [];
  for (const event of events) {
    const rule = classify(event);
    if (rule) alerts.push({ event, ...rule });
  }
  return alerts;
}

export function countByTier(alerts: Alert[]): Record<Tier, number> {
  const counts: Record<Tier, number> = { crit: 0, high: 0, warn: 0, info: 0 };
  for (const alert of alerts) counts[alert.tier] += 1;
  return counts;
}

/**
 * Most serious first, then newest first. Event ids are sortable, so they
 * order by time without parsing timestamps.
 */
export function bySeverity(alerts: Alert[]): Alert[] {
  return [...alerts].sort(
    (a, b) =>
      TIER_RANK[a.tier] - TIER_RANK[b.tier] ||
      b.event.event_id.localeCompare(a.event.event_id),
  );
}

/** Count of alerts at or above `warn`, for the navigation marker. */
export function attentionCount(alerts: Alert[]): number {
  return alerts.filter((alert) => alert.tier !== "info").length;
}
