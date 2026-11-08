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
import { App } from "./App";
import type { Flag, Instance, LedgerEvent } from "./contracts";

beforeEach(() => {
  vi.useFakeTimers();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  window.history.replaceState(null, "", "/");
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.history.replaceState(null, "", "/");
});

const config = {
  burnUrl: "https://burn.example.test",
  localDev: true,
  demoToken: "local-test-token",
};

function goTo(hash: string) {
  window.location.hash = hash;
  window.dispatchEvent(new Event("hashchange"));
}

const today = (h: number, m: number) => {
  const now = new Date();
  return new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
    h,
    m,
  ).toISOString();
};

function instance(
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

function api({
  instances = [] as Instance[],
  events = [] as LedgerEvent[],
  flags = [] as Flag[],
  headers = {} as Record<string, string>,
} = {}) {
  const calls: string[] = [];
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const path = new URL(String(input)).pathname;
    calls.push(path);
    const body =
      path === "/events"
        ? {
            events:
              calls.filter((p) => p === "/events").length === 1 ? events : [],
            agents: [],
          }
        : path === "/instances"
          ? { instances }
          : path === "/roles"
            ? { roles: [{ name: "worker", tools: [], managed_by: "code" }] }
            : path === "/flags"
              ? { flags }
              : undefined;
    if (!body) throw new Error(`Unexpected endpoint ${path}`);
    return new Response(JSON.stringify(body), { headers });
  });
  vi.stubGlobal("fetch", fetcher);
  return {
    count: (path: string) => calls.filter((call) => call === path).length,
  };
}

async function open() {
  await act(async () => {
    render(<App config={config} />);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

describe("the console shell", () => {
  it("has the four places in its navigation and marks the current one", async () => {
    api();
    await open();
    const nav = screen.getByRole("navigation", { name: "Console" });
    expect(
      within(nav)
        .getAllByRole("link")
        .map((link) => link.getAttribute("aria-label") ?? link.textContent),
    ).toEqual(["Overview", "Roles", "Incidents, 0 open", "Credentials"]);
    expect(
      within(nav)
        .getByRole("link", { name: "Overview" })
        .getAttribute("aria-current"),
    ).toBe("page");
    await act(async () => goTo("#/roles/worker"));
    expect(
      within(nav)
        .getByRole("link", { name: "Roles" })
        .getAttribute("aria-current"),
    ).toBe("page");
    // No theme switch, and no word the console never uses.
    expect(screen.queryByRole("button", { name: /theme/i })).toBeNull();
  });

  it("sends old links to the pages that replaced them", async () => {
    api();
    window.history.replaceState(null, "", "/#/burned");
    await open();
    expect(window.location.hash).toBe("#/incidents");
    // The shell's own marker: each page's heading is the page's to write.
    expect(
      screen.getByRole("link", { name: /^Incidents/, current: "page" }),
    ).toBeTruthy();
    await act(async () => goTo("#/alerts"));
    expect(window.location.hash).toBe("#/overview");
    await act(async () => goTo("#/overview?instance=w1"));
    expect(window.location.hash).toBe("#/agents/w1");
  });

  it("polls each shared collection once per interval, whichever page is open", async () => {
    const { count } = api();
    await open();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    for (const path of ["/instances", "/roles", "/flags"])
      expect(count(path)).toBe(5);
    expect(count("/events")).toBe(21);
    await act(async () => goTo("#/incidents"));
    await act(async () => goTo("#/agents/w1"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    for (const path of ["/instances", "/roles", "/flags"])
      expect(count(path)).toBe(7);
  });

  it("counts from one model: the hero and the Incidents marker agree with the data", async () => {
    api({
      instances: [
        instance("w1", "active"),
        instance("w2", "active"),
        instance("w3", "revoked", { last_seen: today(0, 30) }),
      ],
      events: [
        {
          event_id: "evt_01",
          ts: today(0, 31),
          agent_id: "agent-worker-w3",
          session_id: "w3",
          source: "burn",
          action: "revoked",
        },
        {
          event_id: "evt_02",
          ts: today(0, 31),
          agent_id: "agent-worker-w3",
          session_id: "w3",
          source: "burn",
          action: "flagged",
          secret_ref: "burn/shared/crm_db_url",
          flag_level: "reported",
        },
      ],
      flags: [
        {
          secret_ref: "burn/shared/crm_db_url",
          flag_level: "reported",
          instance_count: 1,
          state: "open",
        },
      ],
    });
    await open();
    // The hero's stats, not a legend or a row that also says "Running".
    const stats = document.querySelector("dl.hero-stats") as HTMLElement;
    expect(stats.textContent).toContain("Running2");
    expect(stats.textContent).toContain("Burned today1");
    expect(
      screen.getByRole("link", { name: "Incidents, 1 open" }),
    ).toBeTruthy();
  });

  it("opens Add agents from the navigation", async () => {
    api();
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Add agents" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("signs out of a local run from the person menu", async () => {
    api();
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    sessionStorage.setItem("burn.local.continued", "1");
    await open();
    fireEvent.click(
      screen.getByRole("button", { name: "Signed in as Local development" }),
    );
    fireEvent.click(screen.getByRole("link", { name: "Sign out" }));
    expect(sessionStorage.getItem("burn.local.continued")).toBeNull();
    expect(reload).toHaveBeenCalled();
  });

  it("keeps the mock banner once a development API answers", async () => {
    api({ headers: { "x-burn-mock": "true" } });
    await open();
    expect(screen.getByRole("alert").textContent).toContain("MOCK DATA");
  });

  it("asks for configuration instead of polling without an API", async () => {
    const { count } = api();
    await act(async () => {
      render(<App config={{ burnUrl: "", localDev: true, demoToken: "" }} />);
    });
    expect(screen.getByText("VITE_BURN_API_URL")).toBeTruthy();
    expect(count("/events")).toBe(0);
  });
});
