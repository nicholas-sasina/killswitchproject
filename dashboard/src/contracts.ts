// Mirrors contracts/burn-api.openapi.yaml, ledger-event.schema.json and leaktest.openapi.yaml.
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
  | "issued";

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
    | "provider";
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

export interface EncodingsResponse {
  encodings?: string[];
}
export interface LeakTestRequest {
  agent_id: string;
  secret_ref?: string | null;
  encoding?: string;
  free_text?: string | null;
}
export interface LeakTestResult {
  blocked: boolean;
  encoding: string;
  secret_ref?: string | null;
  destination?: string;
  requests_sent?: number;
  reason?: string | null;
  event_id?: string | null;
}
