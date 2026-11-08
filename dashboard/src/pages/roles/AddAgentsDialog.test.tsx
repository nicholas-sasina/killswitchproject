// @vitest-environment jsdom
// Connect an agent: commands for the harness someone already uses, a wait
// on the shared instance poll, and the join token flow behind a disclosure.
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
import type { Instance, Role } from "../../contracts";
import { ConsoleDataProvider } from "../../data/ConsoleData";
import { AddAgentsDialog } from "./AddAgentsDialog";
import { FULL_ACCESS_TOOLS } from "./model";
import { forgetTokens } from "./tokens";

const API_URL = "https://abc123.execute-api.us-east-1.amazonaws.com";
const config: ApiConfig = {
  burnUrl: API_URL,
  localDev: true,
  demoToken: "local-test-token",
};

const roles: Role[] = [
  {
    name: "reviewer",
    tools: [...FULL_ACCESS_TOOLS],
    credentials: ["aws_key"],
    egress: null,
    managed_by: "ui",
  },
  { name: "reader", tools: ["read_ticket"], managed_by: "code" },
  { name: "alpha", tools: [], managed_by: "scenario" },
];

function instance(session: string, role: string, created: string): Instance {
  return {
    session_id: session,
    agent_id: `agent-${role}-${session}`,
    role,
    status: "active",
    created_at: created,
    last_seen: created,
    exposures: 0,
  };
}

function setup(instances: Instance[] = []) {
  const state = { instances, tokens: [] as string[] };
  const fetcher = vi
    .fn<typeof fetch>()
    .mockImplementation(async (input, init) => {
      const path = new URL(String(input)).pathname;
      const method = init?.method ?? "GET";
      let body: unknown;
      if (path === "/roles" && method === "GET") body = { roles };
      else if (path === "/instances") body = { instances: state.instances };
      else if (path === "/events") body = { events: [], agents: [] };
      else if (path === "/flags") body = { flags: [] };
      else if (path.endsWith("/tokens") && method === "POST") {
        const tokenId = crypto.randomUUID().replaceAll("-", "").slice(0, 16);
        const token = `bjt_${tokenId}.${"b".repeat(43)}`;
        state.tokens.push(token);
        body = {
          token,
          token_id: tokenId,
          role: path.split("/")[2],
          uses: 1,
          uses_left: 1,
          status: "active",
          expires_at: "2026-11-08T13:00:00Z",
        };
      } else throw new Error(`Unexpected endpoint: ${method} ${path}`);
      return new Response(JSON.stringify(body));
    });
  vi.stubGlobal("fetch", fetcher);
  const calls = (path: string) =>
    fetcher.mock.calls.filter(
      ([input]) => new URL(String(input)).pathname === path,
    ).length;
  return { state, calls };
}

function Harness({
  role,
  settings = config,
}: {
  role?: string;
  settings?: ApiConfig;
}) {
  const [api] = useState(() => createApi(settings, () => {}, fetch));
  const [open, setOpen] = useState(true);
  return (
    <ConsoleDataProvider
      api={api}
      config={settings}
      configured
      openAddAgents={() => {}}
    >
      {open && <AddAgentsDialog role={role} onClose={() => setOpen(false)} />}
    </ConsoleDataProvider>
  );
}

async function show(role?: string, settings?: ApiConfig) {
  await act(async () => {
    render(<Harness role={role} settings={settings} />);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

const code = (label: string) =>
  screen.getByLabelText(label, { selector: "pre" }).textContent;

async function press(name: string | RegExp) {
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
  forgetTokens();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("Connect an agent", () => {
  it("connects Claude Code with no role and no form", async () => {
    setup();
    await show();
    expect(
      screen.getByRole("heading", { name: "Connect an agent" }),
    ).toBeTruthy();
    expect(code("Setup commands")).toBe(
      'uv tool install "git+https://github.com/nicholas-sasina/killswitchproject#subdirectory=cli"\n' +
        `export BURN_API_URL=${API_URL}\n` +
        "burn login",
    );
    expect(code("Run command")).toBe("burn run claude");
    expect(
      (screen.getByLabelText("Role (optional)") as HTMLSelectElement)
        .selectedOptions[0].textContent,
    ).toBe("None (Full access, grouped as claude)");
    expect(document.body.textContent).toContain(
      "appears here as claude-01. Burning it also cuts off its model.",
    );
    // No form, and the token flow stays closed.
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(
      screen
        .getByRole("button", {
          name: "For CI, containers or swarms: use a join token",
        })
        .getAttribute("aria-expanded"),
    ).toBe("false");
  });

  it("sets the console's people sign-in before burn login", async () => {
    setup();
    await show(undefined, {
      ...config,
      peopleDomain: "burn-people.auth.us-east-1.amazoncognito.com",
      peopleClientId: "4abc5def6ghi7jkl8mno9pqr0s",
    });
    expect(code("Setup commands")).toBe(
      'uv tool install "git+https://github.com/nicholas-sasina/killswitchproject#subdirectory=cli"\n' +
        `export BURN_API_URL=${API_URL}\n` +
        "export BURN_PEOPLE_DOMAIN=burn-people.auth.us-east-1.amazoncognito.com\n" +
        "export BURN_PEOPLE_CLIENT_ID=4abc5def6ghi7jkl8mno9pqr0s\n" +
        "burn login",
    );
  });

  it("gives each harness its command, and a role adds --role", async () => {
    setup();
    await show();
    await press("Codex");
    expect(code("Run command")).toBe("burn run codex");
    expect(document.body.textContent).toContain(
      "Codex connects to Burn's tools but brings no model yet.",
    );
    await press("Script");
    expect(code("Run command")).toBe("burn run python agent.py");
    await act(async () => {
      fireEvent.change(screen.getByLabelText("Role (optional)"), {
        target: { value: "reviewer" },
      });
    });
    expect(code("Run command")).toBe(
      "burn run --role reviewer python agent.py",
    );
    // Scenario roles take no agents from outside.
    expect(
      [
        ...(screen.getByLabelText("Role (optional)") as HTMLSelectElement)
          .options,
      ].map((option) => option.textContent),
    ).toEqual([
      "None (Full access, grouped as agent)",
      "reviewer · Full access",
      "reader · Limited",
    ]);
  });

  it("opens on the role it was opened for", async () => {
    setup();
    await show("reader");
    expect(code("Run command")).toBe("burn run --role reader claude");
  });

  it("waits on the shared instance poll for the first new agent", async () => {
    const fixture = setup([instance("old", "claude", "2026-11-01T10:00:00Z")]);
    await show();
    expect(
      screen.getByText("Waiting for the first new claude agent…"),
    ).toBeTruthy();
    const before = fixture.calls("/instances");
    fixture.state.instances = [
      ...fixture.state.instances,
      instance("new", "claude", new Date().toISOString()),
    ];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    // One more poll of the shared collection, and no poll of its own.
    expect(fixture.calls("/instances")).toBe(before + 1);
    const status = screen.getByText(/Connected ·/);
    expect(within(status).getByRole("link").textContent).toBe("claude-02");
  });

  it("keeps the join token flow behind a disclosure, showing the token once and never in a command", async () => {
    const fixture = setup();
    await show("reviewer");
    await press("For CI, containers or swarms: use a join token");
    expect(
      (screen.getByLabelText("Role for the token") as HTMLSelectElement).value,
    ).toBe("reviewer");
    await press("Create token");
    const token = fixture.state.tokens[0];
    expect(screen.getByText(token).tagName).toBe("CODE");
    for (const pre of document.querySelectorAll("pre"))
      if (!pre.classList.contains("add-token-value"))
        expect(pre.textContent).not.toContain(token);
    await press("Continue to waiting");
    expect(document.body.textContent).not.toContain(token);
  });
});
