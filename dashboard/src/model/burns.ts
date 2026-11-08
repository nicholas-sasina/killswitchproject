/**
 * Why an agent was burned, worded once for every page: the Overview's
 * hero and Burned today rows, and the Incidents list, say it the same way.
 */
import type { LedgerEvent } from "../contracts";
import type { ConsoleAgent } from "./console";
import { isCanaryRef } from "./keys";
import { personLabel } from "./people";
import { burnReason } from "./sentences";

export type BurnTrigger =
  | { kind: "canary"; ip?: string }
  | { kind: "person"; who: string; reason: string }
  | { kind: "reason"; reason: string }
  | { kind: "unknown" };

function isOwn(event: LedgerEvent, agent: ConsoleAgent): boolean {
  return event.session_id
    ? event.session_id === agent.sessionId || event.session_id === agent.key
    : event.agent_id === agent.agentId;
}

/** The IP a canary use names, if its detail carries one. */
export function canaryIp(event: LedgerEvent | undefined): string | undefined {
  return event?.detail?.match(/from ([0-9a-fA-F.:]+)\)?\s*$/)?.[1];
}

/**
 * What started a burn, from its own `revoked` event and the agent's canary:
 * the canary used outside Burn, a person (with their reason), a reason with
 * no person, or nothing recorded. `events` is newest first.
 */
export function burnTrigger(
  agent: ConsoleAgent,
  events: LedgerEvent[],
): BurnTrigger {
  const revoked = events.find(
    (event) => event.action === "revoked" && isOwn(event, agent),
  );
  const canary = events.find(
    (event) => event.action === "canary_used" && isOwn(event, agent),
  );
  const detail = revoked?.detail ?? agent.burnNote ?? "";
  if (canary || isCanaryRef(revoked?.secret_ref) || /canary/i.test(detail))
    return { kind: "canary", ip: canaryIp(canary) };
  const reason = burnReason(detail);
  const who = revoked?.actor ? personLabel(revoked.actor) : "";
  if (who && who !== "Burn") return { kind: "person", who, reason };
  if (reason) return { kind: "reason", reason };
  return { kind: "unknown" };
}

/** The short form, for a Burned today row: "Its canary was used from outside". */
export function triggerShort(trigger: BurnTrigger): string {
  switch (trigger.kind) {
    case "canary":
      return "Its canary was used from outside";
    case "person":
      return `Burned by ${trigger.who}${trigger.reason ? `: ${trigger.reason}` : ""}`;
    case "reason":
      return `Burned: ${trigger.reason}`;
    case "unknown":
      return "No reason was recorded";
  }
}
