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
  | "dismissed";
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
