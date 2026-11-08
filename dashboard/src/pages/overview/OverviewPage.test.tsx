// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BurnApi } from "../../api";
import type { Flag, Instance, LedgerEvent, Role } from "../../contracts";
import { ConsoleDataContext, type ConsoleData } from "../../data/ConsoleData";
import { mergeFleet } from "../../fleet";
import {
  agentForEvent,
  buildConsole,
  createPulseBus,
  incidentCounts,
  incidents,
  shortTime,
} from "../../model";
import { OverviewPage } from "./OverviewPage";

const now = new Date();
const today = (h: number, m: number, s = 0) =>
  new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
    h,
    m,
    s,
  ).toISOString();

function instance(
  session: string,
  role: string,
  status: Instance["status"] = "active",
  extra: Partial<Instance> = {},
): Instance {
  return {
    session_id: session,
    agent_id: `agent-${role}-${session}`,
    role,
    status,
    created_at: today(0, Number(session.replace(/\D/g, "")) % 60),
    ...extra,
  };
}

const roles: Role[] = [
  { name: "worker", tools: [], managed_by: "code", owner: "Support" },
  { name: "reader", tools: [], managed_by: "ui", owner: "Triage" },
];

function data({
  instances = [] as Instance[],
  events = [] as LedgerEvent[],
  flags = [] as Flag[],
  loaded = true,
  extra = {} as Partial<ConsoleData>,
} = {}): ConsoleData {
  const list = [...events].sort((a, b) => b.event_id.localeCompare(a.event_id));
  const model = buildConsole({
    fleet: mergeFleet([], instances),
    events: list,
    roles,
    now: new Date(),
  });
  const incidentList = incidents(model, list, flags);
  return {
    api: {} as BurnApi,
    config: { burnUrl: "https://burn.example.test", localDev: true },
    configured: true,
    joinUrls: { api: "", gateway: "" },
    events: list,
    eventsError: "",
    updatedAt: loaded ? new Date() : undefined,
    history: loaded
      ? { from: today(0, 0), complete: true }
      : { complete: false },
    instances,
    instancesLoaded: loaded,
    instancesError: "",
    roles,
    rolesLoaded: loaded,
    rolesError: "",
    flags,
    flagsLoaded: loaded,
    flagsError: "",
    model,
    incidents: incidentList,
    incidentCounts: incidentCounts(incidentList, flags),
    sentenceContext: {
      nameOf: ({ agentId, sessionId }) =>
        agentForEvent(model, {
          agent_id: agentId ?? "",
          session_id: sessionId,
        })?.name,
    },
    burns: {},
    recordBurn: () => {},
    noteFlag: () => {},
    refreshFlags: () => {},
    refreshRoles: () => {},
    refreshInstances: () => {},
    openAddAgents: () => {},
    pulses: createPulseBus(),
    ...extra,
  };
}

function show(value: ConsoleData) {
  return render(
    <ConsoleDataContext value={value}>
      <OverviewPage />
    </ConsoleDataContext>,
  );
}

function rerenderWith(
  view: ReturnType<typeof render>,
  value: ConsoleData,
): void {
  view.rerender(
    <ConsoleDataContext value={value}>
      <OverviewPage />
    </ConsoleDataContext>,
  );
}

let seq = 0;
function event(fields: Partial<LedgerEvent>): LedgerEvent {
  seq += 1;
  return {
    event_id: `evt_${String(seq).padStart(6, "0")}`,
    ts: today(0, 30),
    agent_id: "system",
    source: "gateway",
    action: "recorded",
    ...fields,
  };
}

function card(name: string): HTMLElement {
  return screen
    .getByRole("heading", { name: new RegExp(`^${name}`, "i") })
    .closest("section") as HTMLElement;
}

const workers = (n: number, role = "worker") =>
  Array.from({ length: n }, (_, i) => instance(`${role[0]}${i + 1}`, role));

function incident() {
  return data({
    instances: [
      ...workers(3),
      instance("w4", "worker", "revoked", { last_seen: today(0, 40) }),
      ...workers(2, "reader"),
    ],
    events: [
      event({
        ts: today(0, 42),
        agent_id: "agent-worker-w4",
        session_id: "w4",
        source: "provider",
        action: "canary_used",
      }),
      event({
        ts: today(0, 42),
        agent_id: "agent-worker-w4",
        session_id: "w4",
        source: "burn",
        action: "revoked",
      }),
      event({
        ts: today(0, 42),
        agent_id: "agent-worker-w4",
        source: "burn",
        action: "flagged",
        secret_ref: "burn/shared/crm_db_url",
        flag_level: "reported",
      }),
    ],
    flags: [
      {
        secret_ref: "burn/shared/crm_db_url",
        owner_agent_id: "shared",
        flag_level: "reported",
        instance_count: 1,
        state: "open",
      },
    ],
  });
}

beforeEach(() => localStorage.clear());
afterEach(cleanup);

describe("the Overview", () => {
  it("waits for the fleet before it states anything", () => {
    show(data({ loaded: false }));
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "Loading the fleet.",
    );
    expect(
      within(card("Running now")).getByText("Loading agents."),
    ).toBeTruthy();
    expect(within(card("Live")).getByText("Loading events.")).toBeTruthy();
    expect(
      within(card("Last 24 hours")).getByText(
        "Loading the last 24 hours of events.",
      ),
    ).toBeTruthy();
  });

  it("says what will appear when nothing is running", () => {
    show(data());
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "No agents are running.",
    );
    expect(
      within(card("Running now")).getByText(
        "No agents running. Connect an agent to start one.",
      ),
    ).toBeTruthy();
    expect(
      within(card("Needs a decision")).getByText(/^Nothing needs you\./),
    ).toBeTruthy();
    expect(
      within(card("Burned today")).getByText("No agent has been burned today."),
    ).toBeTruthy();
    expect(
      within(card("Live")).getByText(/^Nothing yet\. Events appear here/),
    ).toBeTruthy();
  });

  it("is all clear when nothing was burned today", () => {
    show(data({ instances: [...workers(3), ...workers(2, "reader")] }));
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "All 5 agents are clean.",
    );
    expect(
      screen.queryByRole("link", { name: "Open the incident" }),
    ).toBeNull();
    const now = card("Running now");
    expect(now.querySelector("h2")?.textContent).toBe(
      "Running now / 5 agents in 2 roles",
    );
    expect(
      within(now).getAllByRole("link", { name: /, running$/ }),
    ).toHaveLength(5);
  });

  it("tells an incident, with every count from the same model", () => {
    show(incident());
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "worker-04 was burned.The other 5 kept working.",
    );
    expect(
      screen
        .getByRole("link", { name: "Open the incident" })
        .getAttribute("href"),
    ).toBe("#/agents/w4");
    expect(
      screen
        .getByRole("link", { name: "Review crm_db_url" })
        .getAttribute("href"),
    ).toBe("#/credentials");

    const now = card("Running now");
    expect(now.querySelector("h2")?.textContent).toBe(
      "Running now / 5 agents in 2 roles",
    );
    const worker = within(now).getByRole("group", { name: /^worker agents/ });
    expect(worker.getAttribute("aria-label")).toBe(
      "worker agents: 3 running, 1 burned today",
    );
    expect(
      within(worker).getByRole("link", { name: /^worker-04, burned/ }),
    ).toBeTruthy();
    expect(within(now).getByText("1 burned today")).toBeTruthy();
    expect(
      within(now).getByRole("link", { name: "worker" }).getAttribute("href"),
    ).toBe("#/roles/worker");

    const burned = card("Burned today");
    expect(burned.querySelector("h2")?.textContent).toBe("Burned today / 1");
    const row = within(burned).getByRole("link", { name: /worker-04/ });
    expect(row.getAttribute("href")).toBe("#/agents/w4");
    expect(row.textContent).toContain("Its canary was used from outside");
    expect(row.textContent).toContain("1 key open");
    expect(row.textContent).not.toMatch(/contained/i);

    const decide = card("Needs a decision");
    expect(decide.querySelector("h2")?.textContent).toBe(
      "Needs a decision / 1",
    );
    expect(within(decide).getByText("Shared key / reported")).toBeTruthy();
    // Each Rotate names its key: a card can hold several.
    expect(
      within(decide).getByRole("button", { name: "Rotate crm_db_url" }),
    ).toBeTruthy();
  });

  it("dates a role burn from its revoked events, which name no session", () => {
    // POST /burns revokes the sessions first, so each agent's revoked event
    // carries no session_id (burn_api burn.py). Their last calls were
    // yesterday: only the events can say they were burned today.
    const yesterday = new Date(Date.now() - 36 * 3600 * 1000).toISOString();
    const reason = "worker role burned from the console";
    const at = today(0, 42);
    show(
      data({
        instances: [
          ...workers(2),
          instance("w3", "worker", "revoked", { last_seen: yesterday }),
          instance("w4", "worker", "revoked", { last_seen: yesterday }),
        ],
        events: ["w3", "w4"].map((session) =>
          event({
            ts: at,
            agent_id: `agent-worker-${session}`,
            session_id: null,
            source: "burn",
            action: "revoked",
            detail: `0 session(s) revoked (${reason})`,
          }),
        ),
      }),
    );
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "2 agents were burned today.The other 2 kept working.",
    );
    const stats = document.querySelector("dl.hero-stats") as HTMLElement;
    expect(stats.textContent).toContain("Burned today2");
    expect(screen.getByText(`Incident 02 / ${shortTime(at)}`)).toBeTruthy();
    const worker = screen.getByRole("group", { name: /^worker agents/ });
    expect(worker.getAttribute("aria-label")).toBe(
      "worker agents: 2 running, 2 burned today",
    );
    expect(
      within(worker).getByRole("link", {
        name: `worker-03, burned at ${shortTime(at)}`,
      }),
    ).toBeTruthy();
    const rows = within(card("Burned today")).getAllByRole("link", {
      name: /^worker-0[34]/,
    });
    expect(
      rows.map((row) => row.querySelector(".ov-burned-why")?.textContent),
    ).toEqual([
      `worker, ${shortTime(at)}. Burned: ${reason}`,
      `worker, ${shortTime(at)}. Burned: ${reason}`,
    ]);
  });

  it("sends Register it to Connect an agent and keeps Ignore in this browser", () => {
    const openAddAgents = vi.fn();
    const value = data({
      instances: workers(1),
      events: [
        event({
          ts: today(0, 31),
          action: "refused",
          detail: "unknown badge client 9c1e",
        }),
      ],
      extra: { openAddAgents },
    });
    show(value);
    const decide = card("Needs a decision");
    expect(within(decide).getByText("A badge Burn did not issue")).toBeTruthy();
    fireEvent.click(
      within(decide).getByRole("button", { name: "Register it" }),
    );
    expect(openAddAgents).toHaveBeenCalled();
    fireEvent.click(within(decide).getByRole("button", { name: "Ignore" }));
    expect(within(decide).queryByText("A badge Burn did not issue")).toBeNull();
    expect(localStorage.getItem("burn.console.ignoredCallers")).toContain(
      "caller:badge:9c1e",
    );
  });

  it("shows a flag error inside its card", () => {
    show(data({ extra: { flagsError: "API request failed (HTTP 500)." } }));
    expect(
      within(card("Needs a decision")).getByRole("alert").textContent,
    ).toBe("Flags could not be loaded: API request failed (HTTP 500).");
  });

  it("writes Live as sentences and fades in only rows that arrive later", () => {
    const first = event({
      ts: today(0, 30, 5),
      agent_id: "agent-worker-w1",
      session_id: "w1",
      source: "tool_response",
      action: "recorded",
      secret_ref: "burn/shared/crm_db_url",
      tool: "read_config",
    });
    const view = show(data({ instances: workers(1), events: [first] }));
    const live = card("Live");
    const rows = () => within(live).getAllByRole("listitem");
    expect(rows()[0].textContent).toContain(
      "worker-01 saw crm_db_url through read_config",
    );
    expect(rows()[0].querySelector(".xf-in")).toBeNull();
    const second = event({
      ts: today(0, 30, 6),
      agent_id: "agent-worker-w1",
      session_id: "w1",
      source: "egress",
      action: "blocked",
      secret_ref: "burn/shared/crm_db_url",
      destination: "paste.example",
    });
    rerenderWith(
      view,
      data({ instances: workers(1), events: [first, second] }),
    );
    expect(rows()[0].textContent).toContain(
      "worker-01 tried to send crm_db_url to paste.example; blocked",
    );
    expect(rows()[0].querySelector(".xf-in")).not.toBeNull();
    expect(rows()[1].querySelector(".xf-in")).toBeNull();
  });

  it("keeps 200 squares to one tab stop per role, moved with the arrow keys", () => {
    show(data({ instances: [...workers(160), ...workers(40, "reader")] }));
    const worker = screen.getByRole("group", { name: /^worker agents/ });
    const squares = within(worker).getAllByRole("link");
    expect(squares).toHaveLength(160);
    expect(squares.filter((sq) => sq.tabIndex === 0)).toHaveLength(1);
    squares[0].focus();
    fireEvent.keyDown(squares[0], { key: "ArrowRight" });
    expect(document.activeElement).toBe(squares[1]);
    expect(squares[1].tabIndex).toBe(0);
    expect(squares[0].tabIndex).toBe(-1);
    fireEvent.keyDown(squares[1], { key: "End" });
    expect(document.activeElement).toBe(squares[159]);
  });

  it("leaves Running now untouched when a poll brings events but no new agents", async () => {
    const fleet = [...workers(160), ...workers(40, "reader")];
    const view = show(data({ instances: fleet }));
    const rows = card("Running now").querySelector("ul") as HTMLElement;
    const changes: MutationRecord[] = [];
    const observer = new MutationObserver((list) => changes.push(...list));
    observer.observe(rows, {
      subtree: true,
      childList: true,
      attributes: true,
      characterData: true,
    });
    rerenderWith(
      view,
      data({
        instances: fleet,
        events: [
          event({
            agent_id: "agent-worker-w1",
            session_id: "w1",
            source: "tool_response",
          }),
        ],
      }),
    );
    await act(async () => {});
    observer.disconnect();
    expect(changes).toEqual([]);
    // The new event did reach the page.
    expect(within(card("Live")).getAllByRole("listitem")).toHaveLength(1);
  });
});
