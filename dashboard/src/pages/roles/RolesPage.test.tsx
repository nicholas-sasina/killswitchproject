// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApi, type ApiConfig } from "../../api";
import type { Instance, Role } from "../../contracts";
import { ConsoleDataProvider } from "../../data/ConsoleData";
import { RolesPage } from "./RolesPage";

const config: ApiConfig = {
  burnUrl: "https://burn.example.test",
  localDev: true,
  demoToken: "local-test-token",
};

const minutesAgo = (m: number) =>
  new Date(Date.now() - m * 60_000).toISOString();

const roles: Role[] = [
  {
    name: "worker",
    tools: [],
    owner: "Support automation",
    managed_by: "code",
  },
  { name: "triage", tools: [], managed_by: "ui" },
];

function instance(
  session: string,
  role: string,
  status: Instance["status"],
): Instance {
  return {
    session_id: session,
    agent_id: `agent-${role}-${session}`,
    role,
    status,
    created_at: minutesAgo(30),
    last_seen: minutesAgo(1),
    exposures: 0,
  };
}

const swarm = [
  instance("w1", "worker", "active"),
  instance("w2", "worker", "active"),
  instance("w3", "worker", "revoked"),
  instance("w4", "worker", "exited"),
];

function setup({
  roleList = roles,
  instances = swarm,
  failRoles = false,
  failInstances = false,
  hang = false,
} = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = new URL(String(input)).pathname;
      if (hang) return new Promise<Response>(() => {});
      if (path === "/roles" && failRoles)
        return new Response("down", { status: 503 });
      if (path === "/instances" && failInstances)
        return new Response("down", { status: 503 });
      const body =
        path === "/events"
          ? { events: [], agents: [] }
          : path === "/instances"
            ? { instances }
            : path === "/roles"
              ? { roles: roleList }
              : path === "/flags"
                ? { flags: [] }
                : undefined;
      if (!body) throw new Error(`Unexpected endpoint ${path}`);
      return new Response(JSON.stringify(body));
    }),
  );
}

function Page() {
  const [api] = useState(() => createApi(config, () => {}, fetch));
  const [adding, setAdding] = useState<string>();
  return (
    <ConsoleDataProvider
      api={api}
      config={config}
      configured
      openAddAgents={(role) => setAdding(role ?? "a new role")}
    >
      <RolesPage />
      {adding && <p>Adding to {adding}</p>}
    </ConsoleDataProvider>
  );
}

async function show() {
  await act(async () => {
    render(<Page />);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

const rows = () =>
  [...document.querySelectorAll(".roles-row")].map((row) => [
    row.querySelector("a")?.textContent,
    row.querySelector(".roles-row-line")?.textContent ?? "",
    row.querySelectorAll(".square").length,
    row.querySelector(".roles-row-counts")?.textContent,
  ]);

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "setTimeout"] });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("the roles list", () => {
  it("lists every role with its squares and counts, each linking to its page", async () => {
    setup();
    await show();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Roles");
    expect(document.querySelector(".card-head")?.textContent).toContain(
      "2 kinds of agent",
    );
    expect(rows()).toEqual([
      [
        "worker",
        "Support automation · managed in burn.yaml",
        3,
        "2 running1 burned today",
      ],
      ["triage", "Managed in this dashboard", 0, "0 runningNo agents yet"],
    ]);
    expect(
      screen.getByRole("link", { name: "worker" }).getAttribute("href"),
    ).toBe("#/roles/worker");
    expect(document.querySelector(".roles-row .is-ember")?.textContent).toBe(
      "1 burned today",
    );
  });

  it("opens Add agents for a new role", async () => {
    setup();
    await show();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Add agents" }));
    });
    expect(screen.getByText("Adding to a new role")).toBeTruthy();
  });

  it("says it is loading until roles and agents arrive", async () => {
    setup({ hang: true });
    await show();
    expect(screen.getByText("Loading roles…")).toBeTruthy();
  });

  it("says what to do when there are no roles yet", async () => {
    setup({ roleList: [], instances: [] });
    await show();
    expect(
      screen.getByText(
        "No roles yet. Add agents to create a role and its first join token.",
      ),
    ).toBeTruthy();
  });

  it("still lists the roles its agents name when role records fail", async () => {
    setup({ failRoles: true });
    await show();
    expect(screen.getByRole("alert").textContent).toContain(
      "Role records could not be loaded",
    );
    expect(rows().map((row) => row[0])).toEqual(["worker"]);
  });

  it("says plainly when nothing could be loaded", async () => {
    setup({ failRoles: true, failInstances: true });
    await show();
    expect(screen.getByRole("alert").textContent).toContain(
      "Roles could not be loaded",
    );
  });
});
