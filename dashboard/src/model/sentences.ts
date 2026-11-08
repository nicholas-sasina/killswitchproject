/**
 * Events as plain sentences: "worker-07 tried to send crm_db_url to
 * paste.example; blocked". Each event becomes a subject (`who`) and the rest
 * (`what`), so the Live feed can set the subject in bold and an agent's own
 * timeline can drop it ("Tried to send …").
 *
 * The wording follows what each event actually shows (see alerts.ts for the
 * reasoning behind each kind of block and refusal). An action this build has
 * not heard of still reads as prose instead of breaking the feed.
 */
import type { LedgerEvent } from "../contracts";
import { isCanaryRef, keyName } from "./keys";
import { personLabel } from "./people";

export interface EventSentence {
  /** The subject: an agent's display name, a key, or "An unknown caller". */
  who: string;
  whoKind: "agent" | "key" | "caller" | "burn";
  /** The rest of the sentence, starting lower case. */
  what: string;
  /** Evidence, burning or burned: the ember cases. */
  evidence: boolean;
}

export interface SentenceContext {
  /** The display name for an agent id or session, if the fleet knows it. */
  nameOf?: (ref: {
    agentId?: string | null;
    sessionId?: string | null;
  }) => string | undefined;
}

function key(event: LedgerEvent): string {
  if (isCanaryRef(event.secret_ref)) return "its canary";
  return keyName(event.secret_ref) || "a key";
}

function carriesCredential(event: LedgerEvent): boolean {
  return (
    Boolean(event.secret_ref || event.fingerprint) ||
    event.detail === "credential_detected"
  );
}

/** The reason in a person's burn: "1 session(s) revoked (reason)". */
export function burnReason(detail: string | null | undefined): string {
  const match = detail?.match(/revoked \((.+)\)\s*$/);
  return match ? match[1] : "";
}

function humanize(action: string): string {
  return action.replaceAll("_", " ");
}

export function eventSentence(
  event: LedgerEvent,
  context: SentenceContext = {},
): EventSentence {
  const name = (agentId?: string | null, sessionId?: string | null) =>
    context.nameOf?.({ agentId, sessionId }) ?? agentId ?? "an agent";
  const agent = name(event.agent_id, event.session_id);
  const system = event.agent_id === "system" || !event.agent_id;
  const said = (what: string, evidence = false): EventSentence => ({
    who: agent,
    whoKind: "agent",
    what,
    evidence,
  });
  const tool = event.tool ? ` through ${event.tool}` : "";
  switch (event.action) {
    case "recorded":
      return said(`saw ${key(event)}${tool}`);
    case "inherited":
      return said(
        `was handed ${key(event)}${event.target_agent_id ? ` by ${name(event.target_agent_id)}` : ""}`,
      );
    case "blocked":
      if (!carriesCredential(event))
        return said(
          `was stopped from reaching ${event.destination || "a destination its role does not allow"}`,
        );
      return event.source === "egress"
        ? said(
            `tried to send ${key(event)} ${event.destination ? `to ${event.destination}` : "outside"}; blocked`,
            true,
          )
        : said(
            `tried to pass ${key(event)} to ${event.target_agent_id ? name(event.target_agent_id) : "another agent"}; Burn removed it`,
            true,
          );
    case "refused": {
      const detail = event.detail ?? "";
      if (detail.startsWith("unknown badge") || detail === "session_required")
        return {
          who: "An unknown caller",
          whoKind: "caller",
          what: "was refused before any tool ran",
          evidence: false,
        };
      if (detail === "badge revoked" || detail === "session_inactive")
        return said("was refused: it is burned");
      if (event.source === "tool_response")
        return said(
          `was given a refusal${tool}; the response could not be made safe`,
        );
      return said(
        `asked for ${event.tool || "something"} outside its role; refused`,
      );
    }
    case "revoked": {
      const reason = burnReason(event.detail);
      const actor = personLabel(event.actor);
      if (isCanaryRef(event.secret_ref) || /canary/i.test(event.detail ?? ""))
        return said("burned: its canary was used outside Burn", true);
      if (actor && actor !== "Burn")
        return said(`burned by ${actor}${reason ? `: ${reason}` : ""}`, true);
      return said(`burned${reason ? `: ${reason}` : ""}`, true);
    }
    case "rotated":
      if (system)
        return {
          who: keyName(event.secret_ref) || "A key",
          whoKind: "key",
          what: `rotated by its owner${event.actor ? `, ${personLabel(event.actor)}` : ""}`,
          evidence: false,
        };
      return said(`${key(event)} rotated`);
    case "flagged":
      if (/^rotation (failed|deferred)/.test(event.detail ?? ""))
        return said(
          `${key(event)} could not be rotated; ${event.detail}`,
          true,
        );
      return {
        who: keyName(event.secret_ref) || "A shared key",
        whoKind: "key",
        what:
          event.flag_level === "seen" || !event.flag_level
            ? `flagged for its owner, seen by ${agent}`
            : `flagged for its owner: ${agent} ${event.flag_level === "reported" ? "was burned holding it" : "tried to move it"}`,
        evidence:
          event.flag_level === "attempted" || event.flag_level === "reported",
      };
    case "dismissed":
      return {
        who: keyName(event.secret_ref) || "A flag",
        whoKind: "key",
        what: `flag dismissed by its owner${event.actor ? `, ${personLabel(event.actor)}` : ""}`,
        evidence: false,
      };
    case "rejected":
      return said(
        `presented its old ${key(event)}; the provider refused it`,
        true,
      );
    case "canary_used": {
      const ip = event.detail?.match(/from ([0-9a-fA-F.:]+)\)?\s*$/)?.[1];
      return said(
        `'s canary was used${ip ? ` from ${ip}` : ""}, outside Burn`,
        true,
      );
    }
    case "issued":
      return said(
        event.secret_ref ? `was issued ${key(event)}` : "was issued its badge",
      );
    case "started":
      return said(
        event.parent_session
          ? `was started by ${name(undefined, event.parent_session)}`
          : `joined the ${event.role || "its"} role`,
      );
    case "exited":
      return said("finished");
    case "unregistered":
      return {
        who: "An unknown caller",
        whoKind: "caller",
        what: "was refused: Burn never issued its badge",
        evidence: false,
      };
    case "replaced":
      return said(
        `was replaced by ${event.replaced_by ? name(undefined, event.replaced_by) : "a fresh agent"}`,
      );
    case "resumed":
      return said(
        `took over from ${event.replaces ? name(undefined, event.replaces) : "a burned agent"}`,
      );
    default:
      return said(humanize(String(event.action)));
  }
}

/** The whole sentence, subject first. */
export function eventLine(sentence: EventSentence): string {
  return sentence.what.startsWith("'s ")
    ? `${sentence.who}${sentence.what}`
    : `${sentence.who} ${sentence.what}`;
}

/**
 * The sentence without its subject, for an agent's own timeline:
 * "Tried to send crm_db_url outside; blocked", "Its canary was used …".
 */
export function withoutSubject(sentence: EventSentence): string {
  if (sentence.whoKind !== "agent") return eventLine(sentence);
  const what = sentence.what.startsWith("'s ")
    ? `its ${sentence.what.slice(3)}`
    : sentence.what;
  return what.charAt(0).toUpperCase() + what.slice(1);
}
