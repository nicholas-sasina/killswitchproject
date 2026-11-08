// Hand-maintained mirror of the repository's contracts. When either side
// changes, both must change:
//
//   contracts/ledger-event.schema.json  -> LedgerEvent, EventAction, source
//   contracts/burn-api.openapi.yaml     -> every request and response below
//   contracts/burn.schema.json          -> RoleFields and RoleSpec
//
// If the API ships a new event action before this file mirrors it, the stream
// still renders: EventFeed falls back to readable prose for actions it does
// not know, and alerts.ts treats an unrecognised action as "not an alert"
// rather than guessing a severity. Both degrade quietly instead of breaking,
// so a merge in either order is safe.
export type SecretType = "aws_key" | "api_key" | "other";
export type Owner = "agent" | "shared" | "unknown";
export type EventAction =
  | "recorded"
  | "inherited"
  | "blocked"
  | "refused"
  | "revoked"
  | "rotated"
  | "flagged"
  | "rejected"
  | "issued"
  | "started"
  | "exited"
  | "unregistered"
  | "canary_used"
  | "dismissed"
  | "replaced"
  | "resumed";
export type FlagLevel = "seen" | "attempted" | "reported";

export interface LedgerEvent {
  event_id: string;
  ts: string;
  agent_id: string;
  session_id?: string | null;
  source:
    | "tool_response"
    | "egress"
    | "handoff"
    | "burn"
    | "gateway"
    | "session"
    | "provider"
    | "instance";
  action: EventAction;
  tool?: string | null;
  secret_ref?: string | null;
  fingerprint?: string | null;
  secret_type?: SecretType | null;
  owner?: Owner | null;
  owner_agent_id?: string | null;
  target_agent_id?: string | null;
  destination?: string | null;
  encoding?: string | null;
  detail?: string | null;
  role?: string | null;
  parent_session?: string | null;
  flag_level?: FlagLevel | null;
  // replaced: the session that replaced this burned one. resumed: the burned session this one replaced.
  replaced_by?: string | null;
  replaces?: string | null;
  actor?: string | null;
}

export interface AgentState {
  agent_id: string;
  role?: string | null;
  status: "active" | "revoked" | "idle";
  session_id?: string | null;
  exposures?: number;
}

export interface EventsResponse {
  events: LedgerEvent[];
  agents: AgentState[];
  cursor?: string | null;
}

export interface LedgerEntry {
  secret_ref: string | null;
  fingerprint: string;
  secret_type: SecretType;
  owner: Owner;
  owner_agent_id?: string | null;
  source: "tool_response" | "handoff";
  via?: string | null;
  first_seen: string;
  status: "active" | "rotated" | "flagged";
  flag_level?: FlagLevel | null;
  /** seen: the plaintext reached the agent; referenced: it only ever got a reference. */
  exposure?: "seen" | "referenced";
  /** What the agent read it from: '<tool>:<resource>', '<tool>:sha256:<hex>', or the tool alone. */
  source_ref?: string | null;
}

export interface LedgerResponse {
  agent_id: string;
  entries: LedgerEntry[];
}

export interface BurnRequest {
  agent_id: string;
  reason?: string;
}
export interface BurnResult {
  agent_id: string;
  revoked: boolean;
  sessions_revoked: string[];
  rotated: string[];
  flagged: string[];
}

export interface RoleFields {
  name: string;
  tools: string[];
  credentials?: ("api_key" | "aws_key")[];
  may_message?: string[];
  may_spawn?: string[];
  egress?: string[] | null;
  owner?: string | null;
  canaries?: boolean;
  auto_burn?: boolean;
  replace_on_burn?: boolean;
  /** What the gateway does with a credential in tool output; the API defaults it to replace. */
  references?: "replace" | "report" | "off";
}

export interface RoleSpec extends RoleFields {
  managed_by?: "code" | "ui";
}

export interface Role extends RoleFields {
  managed_by: "code" | "ui" | "scenario";
  created_at?: string;
  updated_at?: string;
  active_instances?: number;
}

export interface EnrollToken {
  token_id: string;
  role: string;
  uses: number;
  uses_left: number;
  expires_at: string;
  status: "active" | "used_up" | "expired" | "revoked";
  created_at?: string;
  created_by?: string | null;
}

export interface Instance {
  session_id: string;
  agent_id: string;
  role: string;
  status: "active" | "revoked" | "exited";
  parent_session?: string | null;
  token_id?: string | null;
  owner?: string | null;
  tools?: string[];
  egress?: string[] | null;
  created_at: string;
  last_seen?: string | null;
  exited_at?: string | null;
  exposures?: number;
  generation?: number;
  replaces?: string | null;
  replaced_by?: string | null;
  replacement?: Replacement | null;
  items_started?: number;
  items_done?: number;
  created_by?: string | null;
}

export type ReplacementStatus = "pending" | "linked" | "needs_person";

// Set on an instance Burn burned when its role has replace_on_burn (never for a role burn).
export interface Replacement {
  status: ReplacementStatus;
  cutoff: string;
  cutoff_source: "evidence" | "burn_time";
  done: string[];
  quarantined: string[];
}

export interface ReplaceRequest {
  session_id: string;
}

export interface ReplaceResult {
  replaces: string;
  session_id: string;
  generation: number;
  cutoff: string;
  cutoff_source: "evidence" | "burn_time";
  done: string[];
  quarantined: string[];
}

export interface ScopedBurnRequest {
  scope: "instance" | "role" | "lineage" | "token";
  id: string;
  reason?: string;
}

export interface ScopedBurnResult {
  scope: string;
  id: string;
  burned: BurnResult[];
  failed?: { agent_id?: string; error?: string }[];
}

export interface BlastEvent {
  ts: string;
  key_ref: string;
  event_name: string;
  event_source?: string;
  resource?: string | null;
  source_ip?: string | null;
  error_code?: string | null;
}

export interface BlastRadius {
  agent_id: string;
  keys: {
    key_ref?: string;
    key_hint?: string;
    status?: "active" | "inactive";
  }[];
  events: BlastEvent[];
  note?: string;
}

export interface BurnBySourceRequest {
  source_ref: string;
  tool?: string;
  since?: string;
  reason?: string;
}

export interface BurnBySourceResult {
  source_ref: string;
  since?: string | null;
  readers: {
    agent_id?: string;
    via?: "read" | "handoff";
    first_seen?: string;
  }[];
  burned: BurnResult[];
  failed?: { agent_id?: string; error?: string }[];
}

/** The first evidence that took a flag to a level. Safe ids only. */
export interface FlagEvidence {
  source?: string;
  event_id?: string;
  agent_id?: string;
  session_id?: string | null;
  tool?: string | null;
  ts?: string;
  recorded_at?: string;
}

/** One flag per credential. Never contains a credential value. */
export interface Flag {
  secret_ref: string;
  secret_type?: SecretType;
  /** "shared", or the agent that owns the credential. */
  owner_agent_id?: string;
  /** The highest level reached; never decreases. */
  flag_level: FlagLevel;
  /** Distinct agents whose evidence flagged it. */
  instance_count: number;
  /** rotated is only ever returned by /flags/resolve; GET /flags drops it. */
  state: "open" | "dismissed" | "rotated";
  flagged_at?: string;
  updated_at?: string;
  evidence?: Partial<Record<FlagLevel, FlagEvidence>>;
  resolution?: {
    action: "rotate" | "dismiss";
    reason: string;
    by?: string | null;
    at: string;
    level?: FlagLevel;
  } | null;
}

export interface FlagResolution {
  secret_ref: string;
  action: "rotate" | "dismiss";
  /** 3 to 300 characters, shown in the event feed. */
  reason: string;
  /** Who decided: ^[A-Za-z0-9 ._@-]{1,64}$ */
  by?: string;
}

export interface FlagResolveResult {
  flag?: Flag;
  rotated_to_version?: number | null;
}
