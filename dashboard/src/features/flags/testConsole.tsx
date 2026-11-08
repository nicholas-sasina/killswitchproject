// Test support only (never imported by the app): a console value built from
// raw API answers with the real model, for rendering the incidents and
// credentials pages and FlagActions inside <ConsoleDataContext>.
import type { ReactNode } from "react";
import { vi } from "vitest";
import type { BurnApi } from "../../api";
import type { AgentState, Flag, Instance, LedgerEvent } from "../../contracts";
import { ConsoleDataContext, type ConsoleData } from "../../data/ConsoleData";
import { mergeFleet } from "../../fleet";
import {
  agentForEvent,
  buildConsole,
  createPulseBus,
  incidentCounts,
  incidents,
} from "../../model";

/** Today at a local time, as the API writes it. */
export function today(hours: number, minutes: number, seconds = 0): string {
  const now = new Date();
  return new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
    hours,
    minutes,
    seconds,
  ).toISOString();
}

let sequence = 0;

/** An event with a sortable id; pass newer events later. */
export function event(fields: Partial<LedgerEvent>): LedgerEvent {
  sequence += 1;
  return {
    event_id: `evt_${String(sequence).padStart(26, "0")}`,
    ts: today(12, 0),
    agent_id: "system",
    source: "burn",
    action: "recorded",
    ...fields,
  };
}

export function instance(
  session: string,
  status: Instance["status"],
  extra: Partial<Instance> = {},
): Instance {
  return {
    session_id: session,
    agent_id: `agent-worker-${session}`,
    role: "worker",
    status,
    created_at: today(0, 1),
    ...extra,
  };
}

export function flag(extra: Partial<Flag> = {}): Flag {
  return {
    secret_ref: "burn/shared/crm_db_url",
    secret_type: "other",
    owner_agent_id: "shared",
    flag_level: "seen",
    instance_count: 1,
    state: "open",
    flagged_at: today(12, 0),
    ...extra,
  };
}

export function consoleValue({
  instances = [],
  agents = [],
  events = [],
  flags = [],
  api = {},
  now = new Date(),
  ...overrides
}: {
  instances?: Instance[];
  agents?: AgentState[];
  /** Oldest first, as written; the console keeps them newest first. */
  events?: LedgerEvent[];
  flags?: Flag[];
  api?: Partial<BurnApi>;
  now?: Date;
} & Omit<Partial<ConsoleData>, "api"> = {}): ConsoleData {
  const newest = [...events].sort((a, b) =>
    b.event_id.localeCompare(a.event_id),
  );
  const shown = flags.filter((item) => item.state !== "rotated");
  const model = buildConsole({
    fleet: mergeFleet(agents, instances),
    events: newest,
    roles: [],
    now,
  });
  const list = incidents(model, newest, shown);
  return {
    api: api as BurnApi,
    config: { burnUrl: "https://burn.example.test" },
    configured: true,
    joinUrls: { api: "https://burn.example.test" },
    events: newest,
    eventsError: "",
    history: { complete: true },
    instances,
    instancesLoaded: true,
    instancesError: "",
    roles: [],
    rolesLoaded: true,
    rolesError: "",
    flags: shown,
    flagsLoaded: true,
    flagsError: "",
    model,
    incidents: list,
    incidentCounts: incidentCounts(list, shown),
    sentenceContext: {
      nameOf: ({ agentId, sessionId }) =>
        agentForEvent(model, {
          agent_id: agentId ?? "",
          session_id: sessionId,
        })?.name,
    },
    burns: {},
    recordBurn: vi.fn(),
    noteFlag: vi.fn(),
    refreshFlags: vi.fn(),
    refreshRoles: vi.fn(),
    refreshInstances: vi.fn(),
    openAddAgents: vi.fn(),
    pulses: createPulseBus(),
    ...overrides,
  };
}

export function WithConsole({
  value,
  children,
}: {
  value: ConsoleData;
  children: ReactNode;
}) {
  return <ConsoleDataContext value={value}>{children}</ConsoleDataContext>;
}
