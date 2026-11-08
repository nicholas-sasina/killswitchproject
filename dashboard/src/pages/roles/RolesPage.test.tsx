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

let posted: Record<string, unknown>[] = [];

function setup({
  roleList = roles,
  instances = swarm,
  failRoles = false,
  failInstances = false,
  hang = false,
} = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const path = new URL(String(input)).pathname;
      if (hang) return new Promise<Response>(() => {});
      if (path === "/roles" && init?.method === "POST") {
        if (JSON.parse(String(init.body)).name === "refused")
          return new Response("bad", { status: 400 });
        posted.push(JSON.parse(String(init.body)));
        return new Response(
          JSON.stringify({ ...posted.at(-1), managed_by: "ui" }),
        );
      }
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
  posted = [];
  window.history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("the roles list", () => {
  it("lists every role with its squares and counts, each linking to its page", async () => {
    setup();
    await show();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Roles");
    expect(document.querySelector(".card-head")?.textContent).toContain(
      "2 groups of agents",
    );
    expect(rows()).toEqual([
      [
        "worker",
        "Limited · Support automation · managed in burn.yaml",
        3,
        "2 running1 burned today",
      ],
      [
        "triage",
        "Limited · managed in this dashboard",
        0,
        "0 runningNo agents yet",
      ],
    ]);
    expect(
      screen.getByRole("link", { name: "worker" }).getAttribute("href"),
    ).toBe("#/roles/worker");
    expect(document.querySelector(".roles-row .is-ember")?.textContent).toBe(
      "1 burned today",
    );
  });

  it("opens Connect an agent", async () => {
    setup();
    await show();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Connect an agent" }));
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
        "No roles yet. Connect an agent and it is grouped by its harness, or make a new role to narrow what agents may do.",
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

describe("a new role", () => {
  async function newRole(name: string) {
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "New role" }));
    });
    fireEvent.change(screen.getByLabelText("Role name"), {
      target: { value: name },
    });
  }
  async function create() {
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Create role" }));
    });
  }

  it("is Full access by default and sends exactly the shared body", async () => {
    setup();
    await show();
    await newRole("reviewer");
    expect(screen.queryByText("Tools")).toBeNull();
    await create();
    expect(posted).toStrictEqual([
      {
        name: "reviewer",
        tools: [
          "list_tickets",
          "read_ticket",
          "read_config",
          "query_customers",
          "send_message",
          "read_inbox",
          "notify",
        ],
        credentials: ["aws_key"],
        may_message: [],
        may_spawn: [],
        egress: null,
        owner: null,
        canaries: true,
        auto_burn: true,
        replace_on_burn: false,
      },
    ]);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(window.location.hash).toBe("#/roles/reviewer");
  });

  it("narrows tools, keys and websites when Limited", async () => {
    setup();
    await show();
    await newRole("triager");
    fireEvent.click(screen.getByLabelText("Limited"));
    fireEvent.click(screen.getByLabelText("send_message"));
    fireEvent.click(screen.getByLabelText("aws_key"));
    fireEvent.change(
      screen.getByLabelText("Websites it can reach (optional)"),
      {
        target: { value: "api.example.com" },
      },
    );
    await create();
    expect(posted[0]).toMatchObject({
      name: "triager",
      tools: [
        "list_tickets",
        "read_ticket",
        "read_config",
        "query_customers",
        "read_inbox",
        "notify",
      ],
      credentials: [],
      egress: ["api.example.com"],
    });
  });

  it("keeps protections under More, with the API's defaults", async () => {
    setup();
    await show();
    await newRole("reviewer");
    expect(screen.queryByLabelText("Canary in every session")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(
      (screen.getByLabelText("Canary in every session") as HTMLInputElement)
        .checked,
    ).toBe(true);
    expect(
      (screen.getByLabelText("Replace a burned agent") as HTMLInputElement)
        .checked,
    ).toBe(false);
    fireEvent.click(screen.getByLabelText("Replace a burned agent"));
    await create();
    expect(posted[0]).toMatchObject({ replace_on_burn: true });
  });

  it("refuses a name kept for evaluations, before writing anything", async () => {
    setup();
    await show();
    await newRole("eval-x");
    await create();
    expect(screen.getByRole("alert").textContent).toContain(
      "kept for Burn's evaluations",
    );
    expect(posted).toEqual([]);
    expect(document.activeElement?.textContent).toBe("Create role");
  });

  it("says plainly when Burn refuses the role", async () => {
    setup();
    await show();
    await newRole("refused");
    await create();
    expect(screen.getByRole("alert").textContent).toBe(
      "Burn refused this role. Check its name and access, then try again.",
    );
  });

  it("ignores websites typed while Full access hides them", async () => {
    setup();
    await show();
    await newRole("reviewer");
    fireEvent.click(screen.getByLabelText("Limited"));
    fireEvent.change(
      screen.getByLabelText("Websites it can reach (optional)"),
      {
        target: { value: "not a host!" },
      },
    );
    fireEvent.click(screen.getByLabelText("Full access"));
    await create();
    expect(posted[0]).toMatchObject({ egress: null });
  });

  it("refuses a name that is taken, before writing anything", async () => {
    setup();
    await show();
    await newRole("worker");
    await create();
    expect(screen.getByRole("alert").textContent).toContain(
      "A role with that name already exists.",
    );
    expect(posted).toEqual([]);
  });
});
