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
import toolContract from "../../../contracts/mcp-tools.json";
import { createApi } from "../api";
import type { Instance, Role } from "../contracts";
import { SwarmPanel } from "./SwarmPanel";
import { roleLineage } from "./InstanceLineage";

function role(name: string, managed_by: Role["managed_by"] = "ui"): Role {
  return { name, managed_by, tools: [], canaries: true, auto_burn: true };
}

function instance(
  id: string,
  roleName = "worker",
  extra: Partial<Instance> = {},
): Instance {
  return {
    session_id: `session_${id}`,
    agent_id: `agent-${roleName}-${id}`,
    role: roleName,
    status: "active",
    created_at: "2026-11-07T12:00:00Z",
    last_seen: "2026-11-07T12:01:00Z",
    exposures: 0,
    ...extra,
  };
}

function setup(
  roles = [role("planner", "code"), role("reader", "scenario")],
  instances: Instance[] = [],
) {
  const state = {
    roles,
    instances,
    failToken: false,
    failBurn: false,
    failPoll: false,
    partialBurn: false,
  };
  const tokens: string[] = [];
  const fetcher = vi
    .fn<typeof fetch>()
    .mockImplementation(async (input, init) => {
      const path = new URL(String(input)).pathname;
      const method = init?.method ?? "GET";
      let body: unknown;
      if (state.failPoll && method === "GET")
        return new Response("unavailable", { status: 503 });
      if (path === "/roles" && method === "GET") body = { roles: state.roles };
      else if (path === "/instances") body = { instances: state.instances };
      else if (path === "/roles" && method === "POST") {
        const created = JSON.parse(String(init?.body)) as Role;
        state.roles = [...state.roles, created];
        body = created;
      } else if (path.endsWith("/tokens")) {
        if (state.failToken)
          return new Response("private response", { status: 503 });
        const tokenId = crypto.randomUUID().replaceAll("-", "").slice(0, 16);
        const token = `bjt_${tokenId}.${"a".repeat(43)}`;
        tokens.push(token);
        body = {
          token,
          token_id: tokenId,
          role: path.split("/")[2],
          ...JSON.parse(String(init?.body)),
          uses_left: 1,
          status: "active",
          expires_at: "2026-11-07T13:00:00Z",
        };
      } else if (path === "/burns") {
        if (state.failBurn)
          return new Response("private response", { status: 503 });
        const request = JSON.parse(String(init?.body));
        const targets = state.instances.filter(
          (row) =>
            row.status === "active" &&
            (request.scope === "role"
              ? row.role === request.id
              : row.session_id === request.id),
        );
        const burned = (state.partialBurn ? targets.slice(0, 1) : targets).map(
          (row) => ({
            agent_id: row.agent_id,
            revoked: true,
            sessions_revoked: [row.session_id],
            rotated: [],
            flagged: [],
          }),
        );
        body = {
          scope: request.scope,
          id: request.id,
          burned,
          failed: state.partialBurn
            ? [{ agent_id: targets[1]?.agent_id, error: "private detail" }]
            : [],
        };
      } else throw new Error(`Unexpected endpoint: ${method} ${path}`);
      return new Response(JSON.stringify(body), {
        headers: { "x-burn-mock": "true" },
      });
    });
  const api = createApi(
    { burnUrl: "http://localhost:8700", demoToken: crypto.randomUUID() },
    () => {},
    fetcher,
  );
  const calls = (path: string, method = "GET") =>
    fetcher.mock.calls.filter(
      ([input, init]) =>
        new URL(String(input)).pathname === path &&
        (init?.method ?? "GET") === method,
    );
  return { state, tokens, api, fetcher, calls };
}

async function show(fixture: ReturnType<typeof setup>, enabled = true) {
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(<SwarmPanel api={fixture.api} enabled={enabled} />);
  });
  return view;
}

function change(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

async function click(name: string) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("agent enrollment", () => {
  it("creates the role, shows and copies the token once, then connects on the shared instance poll", async () => {
    const fixture = setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const log = vi.spyOn(console, "log");
    await show(fixture);
    await click("Add agent");
    const dialog = screen.getByRole("dialog");
    expect(document.activeElement).toBe(
      within(dialog).getByRole("button", { name: "Close" }),
    );
    expect(within(dialog).getAllByRole("checkbox").length).toBe(
      toolContract.tools.length + 2 + 4,
    );
    change("Role name", "worker");
    fireEvent.click(screen.getByLabelText("read_ticket"));
    fireEvent.click(screen.getByLabelText("api_key"));
    fireEvent.click(
      within(screen.getByRole("group", { name: "may_message" })).getByLabelText(
        "reader",
      ),
    );
    fireEvent.click(
      within(screen.getByRole("group", { name: "may_spawn" })).getByLabelText(
        "planner",
      ),
    );
    change("Egress hosts (optional)", "api.example.com, *.example.org");
    change("Owner", "platform");
    change("Uses", "2");
    change("Lifetime (seconds)", "600");
    await click("Create role and token");
    expect(
      JSON.parse(String(fixture.calls("/roles", "POST")[0][1]?.body)),
    ).toEqual({
      name: "worker",
      tools: ["read_ticket"],
      credentials: ["api_key"],
      may_message: ["reader"],
      may_spawn: ["planner"],
      egress: ["api.example.com", "*.example.org"],
      owner: "platform",
      managed_by: "ui",
    });
    expect(
      JSON.parse(
        String(fixture.calls("/roles/worker/tokens", "POST")[0][1]?.body),
      ),
    ).toEqual({ uses: 2, ttl: 600 });
    const command =
      "read -rs BURN_JOIN_TOKEN && export BURN_JOIN_TOKEN   # paste the token, press Enter\nburn run --role worker -- <your agent command>";
    expect(
      screen.getByRole("region", { name: "Join commands" }).textContent,
    ).toContain(command);
    expect(screen.getByText(fixture.tokens[0])).toBeTruthy();
    expect(screen.getByText(/token will not be shown again/)).toBeTruthy();
    await click("Copy token");
    expect(writeText).toHaveBeenCalledWith(fixture.tokens[0]);
    await click("Continue to waiting");
    expect(screen.queryByRole("region", { name: "Join commands" })).toBeNull();
    expect(screen.queryByText(fixture.tokens[0])).toBeNull();
    expect(screen.getByText("Waiting for worker to connect…")).toBeTruthy();
    fixture.state.instances = [
      instance("123abc", "worker", {
        token_id: fixture.tokens[0].slice(4, 20),
      }),
    ];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.getByText("Connected · worker")).toBeTruthy();
    expect(fixture.calls("/roles")).toHaveLength(2);
    expect(fixture.calls("/instances")).toHaveLength(2);
    expect(storage).not.toHaveBeenCalled();
    expect(log).not.toHaveBeenCalled();
    expect(window.location.href).not.toContain(fixture.tokens[0]);
    await click("Close");
    await click("New token for worker");
    expect(screen.queryByRole("region", { name: "Join commands" })).toBeNull();
    expect(fixture.calls("/roles/worker/tokens", "POST")).toHaveLength(1);
  });

  it("creates another token for a ui role without recreating it or accepting an old instance as connected", async () => {
    const fixture = setup([role("worker")], [instance("old")]);
    await show(fixture);
    await click("New token for worker");
    await click("Create token");
    const token = fixture.tokens[0];
    await click("Close");
    await click("New token for worker");
    expect(screen.queryByText(token)).toBeNull();
    await click("Create token");
    expect(fixture.tokens[1]).not.toBe(fixture.tokens[0]);
    await click("Continue to waiting");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.getByText("Waiting for worker to connect…")).toBeTruthy();
    fixture.state.instances.push(
      instance("other", "worker", { token_id: "another-token" }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.getByText("Waiting for worker to connect…")).toBeTruthy();
    fixture.state.instances.push(
      instance("new", "worker", { token_id: fixture.tokens[1].slice(4, 20) }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.getByText("Connected · worker")).toBeTruthy();
    expect(fixture.calls("/roles", "POST")).toHaveLength(0);
  });

  it.each(["Bad Name", "-worker", "Upper", "a".repeat(33)])(
    "rejects invalid role name %s",
    async (name) => {
      const fixture = setup();
      await show(fixture);
      await click("Add agent");
      change("Role name", name);
      await click("Create role and token");
      expect(screen.getByRole("alert").textContent).toContain("Role name must");
      expect(fixture.calls("/roles", "POST")).toHaveLength(0);
    },
  );

  it("retries token creation after a failure without posting the role twice", async () => {
    const fixture = setup();
    fixture.state.failToken = true;
    await show(fixture);
    await click("Add agent");
    change("Role name", "worker");
    await click("Create role and token");
    expect(screen.getByRole("alert").textContent).toContain("HTTP 503");
    expect(screen.queryByText("private response")).toBeNull();
    fixture.state.failToken = false;
    await click("Create token");
    expect(fixture.calls("/roles", "POST")).toHaveLength(1);
    expect(screen.getByRole("region", { name: "Join commands" })).toBeTruthy();
  });

  it("keeps the token out of every copyable join command and copies only the token", async () => {
    const fixture = setup([role("worker")]);
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    await show(fixture);
    await click("New token for worker");
    await click("Create token");
    const commands = screen.getByRole("region", { name: "Join commands" });
    const snippets = within(commands).getAllByRole("code");
    expect(snippets.map((snippet) => snippet.textContent)).toEqual([
      "read -rs BURN_JOIN_TOKEN && export BURN_JOIN_TOKEN   # paste the token, press Enter\nburn run --role worker -- <your agent command>",
      "pbpaste | burn run --role worker --token-stdin -- <your agent command>",
    ]);
    for (const snippet of snippets) {
      expect(snippet.textContent).not.toContain(fixture.tokens[0]);
      expect(snippet.textContent).not.toContain("--token ");
    }
    expect(screen.getByText(fixture.tokens[0]).closest("pre")?.className).toBe(
      "swarm-token",
    );
    await click("Copy token");
    expect(writeText).toHaveBeenCalledExactlyOnceWith(fixture.tokens[0]);
    expect(screen.queryByRole("button", { name: "Copy command" })).toBeNull();
  });
});

describe("role instances", () => {
  it("labels only roles with report-only or no-canary settings", async () => {
    const fixture = setup([
      role("defaults"),
      { name: "omitted", tools: [], managed_by: "code" },
      { ...role("report", "code"), auto_burn: false },
      { ...role("none"), canaries: false },
      { ...role("both", "scenario"), canaries: false, auto_burn: false },
    ]);
    await show(fixture);
    for (const name of ["defaults", "omitted"]) {
      const group = within(
        screen.getByRole("region", { name: `Role ${name}` }),
      );
      expect(group.queryByText("report-only")).toBeNull();
      expect(group.queryByText("no canary")).toBeNull();
    }
    const report = within(screen.getByRole("region", { name: "Role report" }));
    expect(report.getByText("report-only")).toBeTruthy();
    expect(report.queryByText("no canary")).toBeNull();
    const none = within(screen.getByRole("region", { name: "Role none" }));
    expect(none.getByText("no canary")).toBeTruthy();
    expect(none.queryByText("report-only")).toBeNull();
    const both = within(screen.getByRole("region", { name: "Role both" }));
    expect(both.getByText("report-only")).toBeTruthy();
    expect(both.getByText("no canary")).toBeTruthy();
    expect(screen.getAllByText("report-only")).toHaveLength(2);
    expect(screen.getAllByText("no canary")).toHaveLength(2);
  });

  it("groups instances with cross-role parent context, status, last seen and exposures", async () => {
    const parent = instance("parent", "planner");
    const child = instance("child", "worker", {
      parent_session: parent.session_id,
      exposures: 3,
    });
    const grandchild = instance("grand", "worker", {
      parent_session: child.session_id,
      status: "exited",
      last_seen: null,
    });
    const fixture = setup(
      [role("planner", "code"), role("worker"), role("reader", "scenario")],
      [grandchild, child, parent],
    );
    await show(fixture);
    const planner = screen.getByRole("region", { name: "Role planner" });
    const worker = screen.getByRole("region", { name: "Role worker" });
    expect(within(planner).getByText(/Code-managed · read-only/)).toBeTruthy();
    expect(
      within(planner).queryByRole("button", { name: /New token/ }),
    ).toBeNull();
    expect(
      within(screen.getByRole("region", { name: "Role reader" })).getByText(
        /Scenario/,
      ),
    ).toBeTruthy();
    const rows = within(worker).getAllByRole("row").slice(1);
    expect(
      rows.map(
        (row) =>
          within(row).getAllByRole("cell")[0].querySelector("strong")
            ?.textContent,
      ),
    ).toEqual([
      "Parent · planner: agent-planner-parent",
      child.agent_id,
      grandchild.agent_id,
    ]);
    expect(
      rows.map((row) => within(row).getAllByRole("cell")[0].style.paddingLeft),
    ).toEqual(["12px", "30px", "48px"]);
    expect(within(rows[1]).getByText("3")).toBeTruthy();
    expect(rows[1].querySelector("time")?.dateTime).toBe(child.last_seen);
    expect(within(rows[2]).getByText("Not reported")).toBeTruthy();
    expect(
      (within(rows[2]).getByRole("button") as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(within(planner).queryByText(child.agent_id)).toBeNull();
  });

  it("keeps more than twenty instances accessible through role, status and text filters", async () => {
    const instances = Array.from({ length: 25 }, (_, index) =>
      instance(String(index), "worker", {
        status: index % 2 ? "active" : "revoked",
        owner: index === 23 ? "platform" : null,
      }),
    );
    const fixture = setup(
      [role("worker"), role("reader", "scenario")],
      instances,
    );
    await show(fixture);
    expect(
      screen.getAllByRole("button", { name: /^Burn instance/ }),
    ).toHaveLength(25);
    change("Role", "worker");
    change("Status", "active");
    expect(
      screen.getAllByRole("button", { name: /^Burn instance/ }),
    ).toHaveLength(12);
    change("Find instance", "platform");
    expect(
      screen.getAllByRole("button", { name: /^Burn instance/ }),
    ).toHaveLength(1);
    expect(screen.getByText("agent-worker-23")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Role reader" })).toBeNull();
  });

  it("preserves orphan and cyclic lineage without duplicating instances", () => {
    const a = instance("a", "worker", { parent_session: "session_b" });
    const b = instance("b", "worker", { parent_session: "session_a" });
    const orphan = instance("orphan", "worker", { parent_session: "missing" });
    expect(
      new Set(
        roleLineage([a, b, orphan], [a, b, orphan]).map(
          (row) => row.instance.session_id,
        ),
      ),
    ).toEqual(new Set([a.session_id, b.session_id, orphan.session_id]));
  });
});

describe("scoped containment", () => {
  it.each(["instance", "role"] as const)(
    "requires confirmation for a %s burn and sends the contract identifier",
    async (scope) => {
      const current = instance("current");
      const fixture = setup([role("worker")], [current]);
      await show(fixture);
      const action =
        scope === "instance"
          ? `Burn instance ${current.agent_id}`
          : "Burn role worker";
      await click(action);
      expect(fixture.calls("/burns", "POST")).toHaveLength(0);
      if (scope === "instance") {
        expect(
          within(screen.getByRole("dialog")).getByText(current.session_id),
        ).toBeTruthy();
      }
      await click("Cancel");
      expect(fixture.calls("/burns", "POST")).toHaveLength(0);
      await click(action);
      change("Reason (optional)", "Suspicious activity");
      await click("Confirm Burn");
      expect(
        JSON.parse(String(fixture.calls("/burns", "POST")[0][1]?.body)),
      ).toEqual({
        scope,
        id: scope === "instance" ? current.session_id : "worker",
        reason: "Suspicious activity",
      });
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(
        screen.getByRole("status", { name: "Scoped burn result" }).textContent,
      ).toBe("1 sessions revoked.");
      expect(
        within(screen.getByRole("region", { name: "Role worker" })).getByText(
          "revoked",
        ),
      ).toBeTruthy();
      expect(
        (screen.getByRole("button", { name: action }) as HTMLButtonElement)
          .disabled,
      ).toBe(true);
    },
  );

  it("keeps an unconfirmed failed burn open and reports partial results without claiming success for failures", async () => {
    const fixture = setup([role("worker")], [instance("a"), instance("b")]);
    await show(fixture);
    fixture.state.failBurn = true;
    await click("Burn role worker");
    await click("Confirm Burn");
    expect(screen.getByRole("alert").textContent).toContain(
      "Outcome not confirmed",
    );
    expect(screen.getByRole("dialog")).toBeTruthy();
    fixture.state.failBurn = false;
    fixture.state.partialBurn = true;
    await click("Confirm Burn");
    expect(
      screen.getByRole("status", { name: "Scoped burn result" }).textContent,
    ).toContain("1 sessions revoked. 1 burns failed");
    expect(
      (
        screen.getByRole("button", {
          name: "Burn instance agent-worker-b",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
    expect(screen.queryByText("private detail")).toBeNull();
  });
});

describe("shared role and instance polling", () => {
  it("polls each collection once every five seconds, retains data after failure and stops on unmount", async () => {
    const fixture = setup([role("worker")], [instance("a")]);
    const view = await show(fixture);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4_999);
    });
    expect(fixture.calls("/roles")).toHaveLength(1);
    expect(fixture.calls("/instances")).toHaveLength(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(fixture.calls("/roles")).toHaveLength(2);
    expect(fixture.calls("/instances")).toHaveLength(2);
    fixture.state.failPoll = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.getByText("agent-worker-a")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain(
      "Retaining the last received",
    );
    expect(
      (screen.getByRole("button", { name: "Add agent" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    fixture.state.failPoll = false;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.queryByRole("alert")).toBeNull();
    view.unmount();
    const count = fixture.fetcher.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fixture.fetcher).toHaveBeenCalledTimes(count);
  });

  it("does not poll when unconfigured or overlap slow collection requests", async () => {
    const fixture = setup();
    const view = await show(fixture, false);
    expect(fixture.fetcher).not.toHaveBeenCalled();
    view.unmount();
    fixture.api.roles = vi.fn().mockReturnValue(new Promise(() => {}));
    fixture.api.instances = vi.fn().mockReturnValue(new Promise(() => {}));
    await show(fixture);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(fixture.api.roles).toHaveBeenCalledTimes(1);
    expect(fixture.api.instances).toHaveBeenCalledTimes(1);
  });
});
