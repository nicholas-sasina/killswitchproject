// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApi, type ApiConfig } from "../../api";
import type { Instance, LedgerEntry, LedgerEvent, Role } from "../../contracts";
import { ConsoleDataProvider } from "../../data/ConsoleData";
import { RolePage } from "./RolePage";
import { forgetTokens } from "./tokens";

const config: ApiConfig = {
  burnUrl: "https://burn.example.test",
  localDev: true,
  demoToken: "local-test-token",
};

const minutesAgo = (m: number) =>
  new Date(Date.now() - m * 60_000).toISOString();

const worker: Role = {
  name: "worker",
  tools: ["read_ticket", "send_message"],
  credentials: ["api_key", "aws_key"],
  may_spawn: ["reader"],
  owner: "Support automation",
  managed_by: "code",
  replace_on_burn: true,
};

function instance(
  session: string,
  role: string,
  status: Instance["status"],
  createdMinutesAgo: number,
  extra: Partial<Instance> = {},
): Instance {
  return {
    session_id: session,
    agent_id: `agent-${role}-${session}`,
    role,
    status,
    created_at: minutesAgo(createdMinutesAgo),
    last_seen: minutesAgo(0),
    exposures: 0,
    ...extra,
  };
}

// worker-01 finished, worker-02 burned (it started reader-01), worker-03 and
// worker-04 running.
const swarm = [
  instance("w1", "worker", "exited", 40),
  instance("w2", "worker", "revoked", 30, { exposures: 4 }),
  instance("w3", "worker", "active", 20, {
    exposures: 4,
    created_by: "priya@example.test",
  }),
  instance("w4", "worker", "active", 10),
  instance("r1", "reader", "active", 5, { parent_session: "w2" }),
];

function ledger(plain: number, referenced: number): LedgerEntry[] {
  const entry = (exposure: "seen" | "referenced", n: number): LedgerEntry => ({
    secret_ref: `burn/shared/key_${exposure}_${n}`,
    fingerprint: `${exposure}${n}`,
    secret_type: "other",
    owner: "shared",
    source: "tool_response",
    first_seen: minutesAgo(1),
    status: "active",
    exposure,
  });
  return [
    ...Array.from({ length: plain }, (_, n) => entry("seen", n)),
    ...Array.from({ length: referenced }, (_, n) => entry("referenced", n)),
  ];
}

function setup({
  roles = [worker, { name: "reader", tools: [], managed_by: "code" }] as Role[],
  instances = swarm,
  events = [] as LedgerEvent[],
  failInstances = false,
  failRevoke = false,
  hang = false,
} = {}) {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockImplementation(async (input, init) => {
      const path = new URL(String(input)).pathname;
      if (hang) return new Promise<Response>(() => {});
      let body: unknown;
      if (path.startsWith("/tokens/") && init?.method === "DELETE") {
        if (failRevoke) return new Response("down", { status: 503 });
        body = {
          token_id: path.split("/")[2],
          role: "worker",
          uses: 20,
          uses_left: 14,
          expires_at: minutesAgo(-60),
          created_at: minutesAgo(50),
          created_by: "priya@example.test",
          status: "revoked",
        };
      } else if (path === "/events") body = { events, agents: [] };
      else if (path === "/instances")
        return failInstances
          ? new Response("down", { status: 503 })
          : new Response(JSON.stringify({ instances }));
      else if (path === "/roles") body = { roles };
      else if (path === "/flags") body = { flags: [] };
      else if (path.startsWith("/agents/") && path.endsWith("/ledger"))
        body = {
          agent_id: path.split("/")[2],
          entries: path.includes("w3") ? ledger(1, 3) : ledger(0, 4),
        };
      else throw new Error(`Unexpected endpoint ${path}`);
      return new Response(JSON.stringify(body));
    });
  vi.stubGlobal("fetch", fetcher);
  const calls = (pattern: RegExp) =>
    fetcher.mock.calls.filter(([input]) =>
      pattern.test(new URL(String(input)).pathname),
    );
  return { fetcher, calls };
}

function Page({ name }: { name: string }) {
  const [api] = useState(() => createApi(config, () => {}, fetch));
  const [adding, setAdding] = useState<string | undefined>();
  return (
    <ConsoleDataProvider
      api={api}
      config={config}
      configured
      openAddAgents={(role) => setAdding(role ?? "a new role")}
    >
      <RolePage name={name} />
      {adding && <p>Adding to {adding}</p>}
    </ConsoleDataProvider>
  );
}

async function show(name = "worker", hash = `#/roles/${name}`) {
  window.history.replaceState(null, "", `/${hash}`);
  await act(async () => {
    render(<Page name={name} />);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

const table = () => screen.getByRole("table");
const rowNames = () =>
  within(table())
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("link")[0].textContent);

async function pick(label: RegExp) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: label }));
  });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "setTimeout"] });
});
afterEach(() => {
  cleanup();
  forgetTokens();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.history.replaceState(null, "", "/");
});

describe("the role page", () => {
  it("opens on the role with its owner, where it is managed and its counts", async () => {
    setup();
    await show();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "worker",
    );
    expect(document.body.textContent).toContain(
      "Owned by Support automation, managed in burn.yaml.",
    );
    const stats = document.querySelector(".hero-stats")!;
    expect(
      [...stats.querySelectorAll("div")].map((stat) => [
        stat.querySelector("dt")?.textContent,
        stat.querySelector("dd .visually-hidden")?.textContent,
      ]),
    ).toEqual([
      ["Running", "2"],
      ["Burned today", "1"],
    ]);
    expect(screen.getByRole("button", { name: "Add workers" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Burn every worker" }),
    ).toBeTruthy();
    // The tabs count workers only; the reader is not one.
    expect(
      within(screen.getByRole("group", { name: "Show workers" }))
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["Running 2", "Burned 1", "Finished 1", "All 4"]);
  });

  it("lists running agents with another role's child on its own line while its parent is hidden", async () => {
    setup();
    await show();
    expect(rowNames()).toEqual(["reader-01", "worker-03", "worker-04"]);
    const reader = within(table()).getAllByRole("row")[1];
    expect(reader.textContent).toContain(
      "A reader spawned by worker-02 (burned)",
    );
    expect(within(table()).getAllByRole("row")[2].textContent).toContain(
      "priya@example.test",
    );
  });

  it("filters by state, keeps the filter in the address, and indents a child under its parent", async () => {
    setup();
    await show();
    await pick(/^Burned/);
    expect(window.location.hash).toBe("#/roles/worker?show=burned");
    expect(rowNames()).toEqual(["worker-02", "reader-01"]);
    const rows = within(table()).getAllByRole("row").slice(1);
    expect(rows[1].textContent).toContain("A reader spawned by worker-02");
    expect(rows[1].textContent).not.toContain("(burned)");
    expect(
      (rows[1].querySelector(".role-agent") as HTMLElement).style.paddingLeft,
    ).toBe("22px");
    expect(
      within(rows[0]).getByRole("link", { name: /Incident/ }),
    ).toBeTruthy();
    expect(within(rows[0]).queryByRole("button", { name: /Burn/ })).toBeNull();
    await pick(/^Finished/);
    expect(rowNames()).toEqual(["worker-01"]);
    await pick(/^All/);
    expect(rowNames()).toEqual([
      "worker-01",
      "worker-02",
      "reader-01",
      "worker-03",
      "worker-04",
    ]);
    await pick(/^Running/);
    expect(window.location.hash).toBe("#/roles/worker");
  });

  it("starts on the filter the address names", async () => {
    setup();
    await show("worker", "#/roles/worker?show=finished");
    expect(rowNames()).toEqual(["worker-01"]);
  });

  it("says how each agent saw its keys, from its ledger, asking only for agents that hold keys", async () => {
    const fixture = setup();
    await show();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const rows = within(table()).getAllByRole("row").slice(1);
    expect(rows[1].textContent).toContain("1 in plain text, 3 references");
    expect(rows[1].querySelector(".role-hot")).toBeTruthy();
    expect(rows[2].textContent).toContain("Nothing yet");
    // worker-03 is the only shown agent holding keys.
    expect(fixture.calls(/\/ledger$/)).toHaveLength(1);
    await pick(/^Burned/);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(within(table()).getAllByRole("row")[1].textContent).toContain(
      "4 references",
    );
    // A poll that changes nothing asks for no ledger again.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(fixture.calls(/\/ledger$/)).toHaveLength(2);
  });

  it("opens the burn dialog for one agent, and for the whole role", async () => {
    setup();
    await show();
    await pick(/^Burn worker-03$/);
    expect(
      within(screen.getByRole("dialog")).getAllByText(/worker-03/).length,
    ).toBeGreaterThan(0);
    await act(async () => {
      fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    await pick(/^Burn every worker$/);
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("opens Add agents for this role", async () => {
    setup();
    await show();
    await pick(/^Add workers$/);
    expect(screen.getByText("Adding to worker")).toBeTruthy();
  });
});

describe("the role page's other states", () => {
  it("says it is loading until the agents arrive", async () => {
    setup({ hang: true });
    await show();
    expect(screen.getByText("Loading workers…")).toBeTruthy();
  });

  it("says plainly when the agents could not be loaded", async () => {
    setup({ failInstances: true });
    await show();
    expect(screen.getByRole("alert").textContent).toContain(
      "Workers could not be loaded",
    );
  });

  it("says what will appear when a role has no agents yet", async () => {
    setup({ instances: [] });
    await show();
    expect(
      screen.getByText("No workers running. Add workers to start one."),
    ).toBeTruthy();
    expect(
      (
        screen.getByRole("button", {
          name: "Burn every worker",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });

  it("offers no Add for a scenario role, whose agent the scenario starts", async () => {
    setup({
      roles: [{ name: "alpha", tools: [], managed_by: "scenario" }],
      instances: [],
    });
    await show("alpha");
    expect(screen.queryByRole("button", { name: /^Add/ })).toBeNull();
    expect(screen.getByText("No alphas running.")).toBeTruthy();
    expect(document.body.textContent).toContain("Managed in the scenario.");
  });

  it("says when there is no such role", async () => {
    setup();
    await show("nobody");
    expect(screen.getByText(/No role named/).textContent).toBe(
      "No role named nobody.",
    );
    expect(
      screen.getByRole("link", { name: "See every role" }).getAttribute("href"),
    ).toBe("#/roles");
  });
});

describe("what the role may do, its protections and its join tokens", () => {
  const TOKEN = "7ff596f9431cd7eb";
  const joined = [
    instance("w3", "worker", "active", 20, { token_id: TOKEN }),
    instance("w4", "worker", "active", 10, { token_id: TOKEN }),
  ];
  const sawShared: LedgerEvent = {
    event_id: "evt_01",
    ts: minutesAgo(1),
    agent_id: "agent-worker-w3",
    session_id: "w3",
    source: "tool_response",
    action: "recorded",
    secret_ref: "burn/shared/crm_db_url",
    owner: "shared",
  };
  const card = (name: RegExp) => screen.getByRole("region", { name });

  it("lists its tools, its own and shared keys, its reach and the roles it may start", async () => {
    setup({ instances: joined, events: [sawShared] });
    await show();
    const may = card(/What a worker may do/i);
    expect(
      within(may)
        .getAllByText(/^(read_ticket|send_message)$/)
        .map((chip) => chip.textContent),
    ).toEqual(["read_ticket", "send_message"]);
    expect(may.textContent).toContain(
      "api_key, aws_key each worker gets its own copy",
    );
    expect(may.textContent).toContain(
      "crm_db_url shared, delivered as a reference",
    );
    expect(may.textContent).toContain(
      "The default allowlist and Burn's Gateway.",
    );
    expect(may.textContent).toContain(
      "reader agents, with at most a worker's tools",
    );
    expect(
      within(may).getByRole("link", { name: "reader" }).getAttribute("href"),
    ).toBe("#/roles/reader");
  });

  it("states each protection in words, with the API's defaults", async () => {
    setup();
    await show();
    const protections = card(/Protections/i);
    expect(
      [...protections.querySelectorAll("dd")].map((dd) => dd.textContent),
    ).toEqual(["On", "On", "On", "On"]);
    expect(protections.textContent).toContain("Replace a burned worker");
  });

  it("lists the token its workers joined with, and revokes it", async () => {
    const fixture = setup({ instances: joined });
    await show();
    const tokens = card(/Join tokens/i);
    expect(tokens.textContent).toContain("7ff596f9");
    expect(tokens.textContent).toMatch(/2 workers joined with it, first at/);
    // A role managed in code takes tokens from Add workers, not New token.
    expect(
      within(tokens).queryByRole("button", { name: "New token" }),
    ).toBeNull();
    await act(async () => {
      fireEvent.click(
        within(tokens).getByRole("button", { name: "Revoke token 7ff596f9" }),
      );
    });
    expect(fixture.calls(new RegExp(`/tokens/${TOKEN}$`))).toHaveLength(1);
    expect(card(/Join tokens/i).textContent).toContain("priya@example.test");
    expect(card(/Join tokens/i).textContent).toContain("Revoked.");
    expect(
      within(card(/Join tokens/i)).queryByRole("button", { name: /Revoke/ }),
    ).toBeNull();
  });

  it("says plainly when a revoke did not go through", async () => {
    setup({ instances: joined, failRevoke: true });
    await show();
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Revoke token 7ff596f9" }),
      );
    });
    expect(screen.getByRole("alert").textContent).toContain(
      "Token 7ff596f9 was not revoked.",
    );
    expect(
      screen.getByRole("button", { name: "Revoke token 7ff596f9" }),
    ).toBeTruthy();
  });

  it("offers New token for a role made in the dashboard", async () => {
    setup({ roles: [{ ...worker, managed_by: "ui" }], instances: [] });
    await show();
    const tokens = card(/Join tokens/i);
    expect(tokens.textContent).toContain(
      "No join tokens used yet. Add workers to make one.",
    );
    await act(async () => {
      fireEvent.click(
        within(tokens).getByRole("button", { name: "New token" }),
      );
    });
    expect(screen.getByText("Adding to worker")).toBeTruthy();
  });
});
