/**
 * Test support only: a ready-made console for the burn dialog's and the
 * agent page's tests, built with the real model so names and counts are the
 * ones the console would show. Nothing in the app imports this file.
 */
import { vi } from "vitest";
import type { ApiConfig, BurnApi } from "../../api";
import type {
  AgentState,
  Flag,
  Instance,
  LedgerEvent,
  Role,
} from "../../contracts";
import type { ConsoleData } from "../../data/ConsoleData";
import { markRevoked, mergeFleet } from "../../fleet";
import { createPulseBus } from "../../model/activity";
import { agentForEvent, buildConsole } from "../../model/console";
import { incidentCounts, incidents } from "../../model/incidents";

export const NOW = new Date("2026-11-08T19:45:00Z");

const at = (minutesBefore: number) =>
  new Date(NOW.valueOf() - minutesBefore * 60_000).toISOString();

export const ROLES: Role[] = [
  {
    name: "worker",
    tools: ["read_ticket", "read_config", "send_message"],
    credentials: ["api_key", "aws_key"],
    may_spawn: ["reader"],
    owner: "Support automation",
    canaries: true,
    auto_burn: true,
    replace_on_burn: true,
    managed_by: "code",
  },
  {
    name: "reader",
    tools: ["read_ticket"],
    credentials: ["api_key"],
    owner: "Triage",
    managed_by: "ui",
  },
];

function worker(n: number, over: Partial<Instance> = {}): Instance {
  return {
    session_id: `sess-w${n}`,
    agent_id: `agent-worker-${n}`,
    role: "worker",
    status: "active",
    token_id: "tok_8f2a",
    created_by: "priya@example.com",
    owner: "Support automation",
    created_at: at(60 - n),
    last_seen: at(1),
    ...over,
  };
}

/**
 * worker-01 finished; worker-02 runs; worker-03 runs and started reader-01;
 * worker-04 was burned and worker-05 took over from it.
 */
export function sampleInstances(): Instance[] {
  return [
    worker(1, { status: "exited", exited_at: at(40), last_seen: at(40) }),
    worker(2),
    worker(3),
    {
      session_id: "sess-r1",
      agent_id: "agent-reader-1",
      role: "reader",
      status: "active",
      parent_session: "sess-w3",
      created_at: at(30),
      last_seen: at(1),
    },
    worker(4, {
      status: "revoked",
      last_seen: at(10),
      replaced_by: "sess-w5",
      items_started: 8,
      items_done: 6,
      replacement: {
        status: "linked",
        cutoff: at(11),
        cutoff_source: "evidence",
        done: ["w4-01", "w4-02", "w4-03", "w4-04", "w4-05", "w4-06"],
        quarantined: ["w4-07", "w4-08"],
      },
    }),
    worker(5, { created_at: at(9), replaces: "sess-w4", generation: 1 }),
  ];
}

export function event(
  over: Partial<LedgerEvent> & Pick<LedgerEvent, "action" | "agent_id">,
): LedgerEvent {
  return {
    event_id: `evt_${over.ts ?? NOW.toISOString()}_${over.action}_${over.secret_ref ?? ""}`,
    ts: NOW.toISOString(),
    source: "gateway",
    ...over,
  };
}

/** worker-04's burn, as the API records it. */
export function sampleEvents(): LedgerEvent[] {
  return [
    event({
      action: "started",
      agent_id: "agent-worker-4",
      session_id: "sess-w4",
      source: "instance",
      role: "worker",
      actor: "priya@example.com",
      ts: at(56),
    }),
    event({
      action: "recorded",
      agent_id: "agent-worker-4",
      session_id: "sess-w4",
      source: "tool_response",
      tool: "read_config",
      secret_ref: "burn/shared/crm_db_url",
      owner: "shared",
      ts: at(14),
    }),
    event({
      action: "blocked",
      agent_id: "agent-worker-4",
      session_id: "sess-w4",
      source: "egress",
      secret_ref: "burn/shared/crm_db_url",
      destination: "paste.example",
      ts: at(11),
    }),
    event({
      action: "revoked",
      agent_id: "agent-worker-4",
      session_id: "sess-w4",
      source: "burn",
      actor: "priya@example.com",
      detail: "1 session(s) revoked (read an unverified vendor page)",
      ts: at(10),
    }),
    event({
      action: "rotated",
      agent_id: "agent-worker-4",
      source: "burn",
      secret_ref: "burn/agent-worker-4/api_key",
      ts: at(10),
    }),
    event({
      action: "rotated",
      agent_id: "agent-worker-4",
      source: "burn",
      secret_ref: "burn/agent-worker-4/aws_key",
      ts: at(10),
    }),
    event({
      action: "flagged",
      agent_id: "agent-worker-4",
      source: "burn",
      secret_ref: "burn/shared/crm_db_url",
      flag_level: "attempted",
      ts: at(10),
    }),
  ].sort((a, b) => b.event_id.localeCompare(a.event_id));
}

export function stubApi(over: Partial<BurnApi> = {}): BurnApi {
  return {
    ledger: vi.fn(async (id: string) => ({ agent_id: id, entries: [] })),
    blastRadius: vi.fn(async (id: string) => ({
      agent_id: id,
      keys: [],
      events: [],
    })),
    burnScope: vi.fn(async () => {
      throw new Error("not stubbed");
    }),
    burn: vi.fn(async () => {
      throw new Error("not stubbed");
    }),
    ...over,
  } as unknown as BurnApi;
}

export function makeConsole({
  instances = sampleInstances(),
  agents = [],
  events = sampleEvents(),
  roles = ROLES,
  flags = [],
  api = stubApi(),
  burnedSessions = [],
  ...over
}: {
  instances?: Instance[];
  agents?: AgentState[];
  events?: LedgerEvent[];
  roles?: Role[];
  flags?: Flag[];
  api?: BurnApi;
  burnedSessions?: string[];
} & Partial<ConsoleData> = {}): ConsoleData {
  const model = buildConsole({
    fleet: markRevoked(mergeFleet(agents, instances), new Set(burnedSessions)),
    events,
    roles,
    now: NOW,
  });
  const list = incidents(model, events, flags);
  return {
    api,
    config: { burnUrl: "http://burn.test", localDev: true } as ApiConfig,
    configured: true,
    joinUrls: { api: "http://burn.test", gateway: "" },
    events,
    eventsError: "",
    updatedAt: NOW,
    history: { complete: true },
    instances,
    instancesLoaded: true,
    instancesError: "",
    roles,
    rolesLoaded: true,
    rolesError: "",
    flags,
    flagsLoaded: true,
    flagsError: "",
    model,
    incidents: list,
    incidentCounts: incidentCounts(list, flags),
    sentenceContext: {
      nameOf: ({ agentId, sessionId }) =>
        agentForEvent(model, { agent_id: agentId ?? "", session_id: sessionId })
          ?.name,
    },
    burns: {},
    recordBurn: vi.fn(),
    noteFlag: vi.fn(),
    refreshFlags: vi.fn(),
    refreshRoles: vi.fn(),
    refreshInstances: vi.fn(),
    openAddAgents: vi.fn(),
    pulses: createPulseBus(),
    ...over,
  };
}
