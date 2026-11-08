// @vitest-environment jsdom
// Add agents: create a role and a join token, show the token once, and
// hand out join commands that never contain it.
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
import toolContract from "../../../../contracts/mcp-tools.json";
import { createApi, type ApiConfig } from "../../api";
import type { Instance, Role } from "../../contracts";
import { ConsoleDataProvider, useConsole } from "../../data/ConsoleData";
import { AddAgentsDialog } from "./AddAgentsDialog";

const API_URL = "https://abc123.execute-api.us-east-1.amazonaws.com";
const GATEWAY_URL =
  "https://gw-1.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp";
const EXPORT_API = `export BURN_API_URL=${API_URL}\n`;

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
  const state = { roles, instances, failToken: false };
  const tokens: string[] = [];
  const fetcher = vi
    .fn<typeof fetch>()
    .mockImplementation(async (input, init) => {
      const path = new URL(String(input)).pathname;
      const method = init?.method ?? "GET";
      let body: unknown;
      if (path === "/roles" && method === "GET") body = { roles: state.roles };
      else if (path === "/instances") body = { instances: state.instances };
      else if (path === "/events") body = { events: [], agents: [] };
      else if (path === "/flags") body = { flags: [] };
      else if (path === "/roles" && method === "POST") {
        const created = JSON.parse(String(init?.body)) as Role;
        if (state.roles.some((role) => role.name === created.name)) {
          return new Response("conflict", { status: 409 });
        }
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
      } else throw new Error(`Unexpected endpoint: ${method} ${path}`);
      return new Response(JSON.stringify(body), {
        headers: { "x-burn-mock": "true" },
      });
    });
  const calls = (path: string, method = "GET") =>
    fetcher.mock.calls.filter(
      ([input, init]) =>
        new URL(String(input)).pathname === path &&
        (init?.method ?? "GET") === method,
    );
  return { state, tokens, fetcher, calls };
}

/** What the shell does: an Add agents button, and New token per role. */
function Openers({ onOpen }: { onOpen: (role?: string) => void }) {
  const { roles } = useConsole();
  return (
    <>
      <button type="button" onClick={() => onOpen()}>
        Add agent
      </button>
      {roles.map((item) => (
        <button key={item.name} type="button" onClick={() => onOpen(item.name)}>
          New token for {item.name}
        </button>
      ))}
    </>
  );
}

function Harness({ config }: { config: ApiConfig }) {
  const [adding, setAdding] = useState<{ role?: string } | null>(null);
  const [api] = useState(() => createApi(config, () => {}, fetch));
  return (
    <ConsoleDataProvider
      api={api}
      config={config}
      configured
      openAddAgents={(name) => setAdding({ role: name })}
    >
      <Openers onOpen={(name) => setAdding({ role: name })} />
      {adding && (
        <AddAgentsDialog role={adding.role} onClose={() => setAdding(null)} />
      )}
    </ConsoleDataProvider>
  );
}

async function show(fixture: ReturnType<typeof setup>, gatewayUrl?: string) {
  vi.stubGlobal("fetch", fixture.fetcher);
  const config: ApiConfig = {
    burnUrl: API_URL,
    gatewayUrl,
    localDev: true,
    demoToken: "local-test-token",
  };
  await act(async () => {
    render(<Harness config={config} />);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
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
    await show(fixture);
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const log = vi.spyOn(console, "log");
    await click("Add agent");
    const dialog = screen.getByRole("dialog");
    expect(document.activeElement).toBe(
      within(dialog).getByRole("button", { name: "Close" }),
    );
    expect(within(dialog).getAllByRole("checkbox").length).toBe(
      toolContract.tools.length + 2 + 4 + 3,
    );
    expect(
      (screen.getByLabelText("Plant a canary credential") as HTMLInputElement)
        .checked,
    ).toBe(true);
    const autoBurn = screen.getByLabelText(
      "Burn automatically if the canary is used",
    ) as HTMLInputElement;
    expect(autoBurn.checked).toBe(true);
    expect(autoBurn.disabled).toBe(false);
    expect(
      (screen.getByLabelText("Replace on burn") as HTMLInputElement).checked,
    ).toBe(false);
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
      canaries: true,
      auto_burn: true,
      replace_on_burn: false,
      managed_by: "ui",
    });
    expect(
      JSON.parse(
        String(fixture.calls("/roles/worker/tokens", "POST")[0][1]?.body),
      ),
    ).toEqual({ uses: 2, ttl: 600 });
    const command = `${EXPORT_API}read -rs BURN_JOIN_TOKEN && export BURN_JOIN_TOKEN   # paste the token, press Enter\nburn run --role worker -- <your agent command>`;
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
    // The dialog reads the console's one instance poll; it never polls.
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
    fixture.state.instances = [
      ...fixture.state.instances,
      instance("other", "worker", { token_id: "another-token" }),
    ];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.getByText("Waiting for worker to connect…")).toBeTruthy();
    fixture.state.instances = [
      ...fixture.state.instances,
      instance("new", "worker", { token_id: fixture.tokens[1].slice(4, 20) }),
    ];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(screen.getByText("Connected · worker")).toBeTruthy();
    expect(fixture.calls("/roles", "POST")).toHaveLength(0);
  });

  it("sends the enabled replacement setting when creating a role", async () => {
    const fixture = setup();
    await show(fixture);
    await click("Add agent");
    change("Role name", "worker");
    fireEvent.click(screen.getByLabelText("Replace on burn"));
    await click("Create role and token");
    expect(
      JSON.parse(String(fixture.calls("/roles", "POST")[0][1]?.body)),
    ).toMatchObject({ replace_on_burn: true });
  });

  it("rejects an existing name before writing a role", async () => {
    const fixture = setup([role("worker")]);
    await show(fixture);
    await click("Add agent");
    change("Role name", "worker");
    await click("Create role and token");
    expect(screen.getByRole("alert").textContent).toContain("already exists");
    expect(fixture.calls("/roles", "POST")).toHaveLength(0);
    expect(
      fixture.fetcher.mock.calls.some(([, init]) => init?.method === "PUT"),
    ).toBe(false);
  });

  it("shows a 409 when another operator creates the name after the role list loads", async () => {
    const fixture = setup();
    await show(fixture);
    await click("Add agent");
    change("Role name", "worker");
    fireEvent.click(screen.getByLabelText("Replace on burn"));
    const existing: Role = {
      ...role("worker"),
      tools: ["read_ticket"],
      egress: ["first.example.test"],
    };
    fixture.state.roles = [...fixture.state.roles, existing];
    await click("Create role and token");
    expect(screen.getByRole("alert").textContent).toContain(
      "A role with that name already exists",
    );
    expect(fixture.calls("/roles", "POST")).toHaveLength(1);
    expect(
      fixture.fetcher.mock.calls.some(([, init]) => init?.method === "PUT"),
    ).toBe(false);
    expect(
      fixture.state.roles.filter((role) => role.name === "worker"),
    ).toEqual([existing]);
    expect(fixture.tokens).toHaveLength(0);
  });

  it.each([
    { canaries: true, auto_burn: false },
    { canaries: false, auto_burn: true },
    { canaries: false, auto_burn: false },
  ])(
    "sends canaries=$canaries and auto_burn=$auto_burn when creating a role",
    async (settings) => {
      const fixture = setup();
      await show(fixture);
      await click("Add agent");
      change("Role name", "worker");
      const canary = screen.getByLabelText("Plant a canary credential");
      const autoBurn = screen.getByLabelText(
        "Burn automatically if the canary is used",
      ) as HTMLInputElement;
      if (!settings.auto_burn) fireEvent.click(autoBurn);
      if (!settings.canaries) fireEvent.click(canary);
      expect(autoBurn.disabled).toBe(!settings.canaries);
      expect(autoBurn.checked).toBe(settings.auto_burn);
      await click("Create role and token");
      expect(
        JSON.parse(String(fixture.calls("/roles", "POST")[0][1]?.body)),
      ).toMatchObject(settings);
    },
  );

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
    await show(fixture);
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    await click("New token for worker");
    await click("Create token");
    const commands = screen.getByRole("region", { name: "Join commands" });
    const snippets = within(commands).getAllByRole("code");
    expect(snippets.map((snippet) => snippet.textContent)).toEqual([
      `${EXPORT_API}read -rs BURN_JOIN_TOKEN && export BURN_JOIN_TOKEN   # paste the token, press Enter\nburn run --role worker -- <your agent command>`,
      `${EXPORT_API}pbpaste | burn run --role worker --token-stdin -- <your agent command>`,
    ]);
    for (const snippet of snippets) {
      expect(snippet.textContent).not.toContain(fixture.tokens[0]);
      expect(snippet.textContent).not.toContain("--token ");
    }
    expect(
      within(commands).queryByText(fixture.tokens[0], { exact: false }),
    ).toBeNull();
    expect(screen.getByText(fixture.tokens[0]).closest("pre")).toBeTruthy();
    await click("Copy token");
    expect(writeText).toHaveBeenCalledExactlyOnceWith(fixture.tokens[0]);
    expect(screen.queryByRole("button", { name: "Copy command" })).toBeNull();
  });

  async function joinSnippets(gatewayUrl: string) {
    const fixture = setup([role("worker")]);
    await show(fixture, gatewayUrl);
    await click("New token for worker");
    await click("Create token");
    const commands = screen.getByRole("region", { name: "Join commands" });
    const snippets = within(commands)
      .getAllByRole("code")
      .map((snippet) => snippet.textContent ?? "");
    return { fixture, commands, snippets };
  }

  it("sets the API and gateway URLs in both join commands, and never the token", async () => {
    const { fixture, snippets } = await joinSnippets(GATEWAY_URL);
    const exports = `${EXPORT_API}export BURN_GATEWAY_URL=${GATEWAY_URL}\n`;
    expect(snippets).toEqual([
      `${exports}read -rs BURN_JOIN_TOKEN && export BURN_JOIN_TOKEN   # paste the token, press Enter\nburn run --role worker -- <your agent command>`,
      `${exports}pbpaste | burn run --role worker --token-stdin -- <your agent command>`,
    ]);
    for (const snippet of snippets) {
      expect(snippet).not.toContain(fixture.tokens[0]);
      expect(snippet).not.toContain("--token ");
    }
  });

  it("leaves the gateway line out of both commands when it is not configured", async () => {
    const { snippets } = await joinSnippets("");
    expect(snippets).toHaveLength(2);
    for (const snippet of snippets) {
      expect(snippet.startsWith(EXPORT_API)).toBe(true);
      expect(snippet).not.toContain("BURN_GATEWAY_URL");
    }
  });

  it("links to the CLI's Connect your agent section in a new tab, so the token stays on screen", async () => {
    const { commands } = await joinSnippets(GATEWAY_URL);
    const guide = within(commands).getByRole("link", {
      name: "Connect your agent",
    });
    expect(guide.getAttribute("href")).toBe(
      "https://github.com/nicholas-sasina/killswitchproject/blob/main/cli/README.md#connect-your-agent",
    );
    expect(guide.getAttribute("target")).toBe("_blank");
    expect(guide.getAttribute("rel")).toContain("noopener");
  });
});
