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

beforeEach(() => {
  vi.useFakeTimers();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.location.hash = "";
});

/** Views are addressable, so a test can move between them. */
function goTo(view: string) {
  window.location.hash = `#/${view}`;
  window.dispatchEvent(new Event("hashchange"));
}

describe("containment dashboard", () => {
  it("requires confirmation before Burn, shows its real result and loads the timeline", async () => {
    const id = `agent-${crypto.randomUUID()}`;
    const ref = `burn/${id}/api_key`;
    let session = crypto.randomUUID();
    let revoked = false;
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async (input, init) => {
        const path = new URL(String(input)).pathname;
        let body: unknown;
        if (path === "/events")
          body = {
            events: [],
            agents: [
              {
                agent_id: id,
                role: "Reader",
                status: revoked ? "revoked" : "active",
                session_id: session,
                exposures: 1,
              },
            ],
          };
        else if (path === "/roles") body = { roles: [] };
        else if (path === "/instances") body = { instances: [] };
        else if (path.endsWith("/ledger")) body = { agent_id: id, entries: [] };
        else if (path.endsWith("/blast-radius"))
          body = { agent_id: id, keys: [], events: [] };
        else if (path === "/burn") {
          expect(init?.method).toBe("POST");
          expect(JSON.parse(String(init?.body))).toEqual({
            agent_id: id,
            reason: "Suspicious activity",
          });
          revoked = true;
          body = {
            agent_id: id,
            revoked: true,
            sessions_revoked: [session],
            rotated: [ref],
            flagged: [],
          };
        } else throw new Error("Unexpected endpoint");
        return new Response(JSON.stringify(body), {
          headers: { "x-burn-mock": "true" },
        });
      });
    vi.stubGlobal("fetch", fetcher);
    await act(async () => {
      render(
        <App
          config={{
            burnUrl: "https://burn.example.test",
            localDev: true,
            demoToken: crypto.randomUUID(),
          }}
        />,
      );
    });
    fireEvent.click(screen.getByRole("button", { name: `Burn ${id}` }));
    const dialog = screen.getByRole("dialog");
    expect(document.activeElement).toBe(
      within(dialog).getByRole("button", { name: "Cancel" }),
    );
    expect(
      fetcher.mock.calls.some((call) => String(call[0]).endsWith("/burn")),
    ).toBe(false);
    fireEvent.change(within(dialog).getByLabelText(/Reason/), {
      target: { value: "Suspicious activity" },
    });
    await act(async () => {
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Confirm Burn" }),
      );
    });
    const outcome = screen.getByRole("status", { name: "Burn result" });
    expect(outcome.textContent).toContain(ref);
    expect(outcome.textContent).toContain("1 session revoked");
    expect(screen.getByText("MOCK DATA")).toBeTruthy();
    expect(
      fetcher.mock.calls.some((call) =>
        String(call[0]).endsWith("/blast-radius"),
      ),
    ).toBe(true);
    expect(screen.getByText(/CloudTrail event history can lag/)).toBeTruthy();
    session = crypto.randomUUID();
    revoked = false;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(
      (screen.getByRole("button", { name: `Burn ${id}` }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
    expect(screen.queryByRole("status", { name: "Burn result" })).toBeNull();
  });

  it("renders nullable agent, ledger and CloudTrail metadata", async () => {
    const id = `agent-${crypto.randomUUID()}`;
    const live = `agent-${crypto.randomUUID()}`;
    const ref = `burn/${id}/aws_key#v1`;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = new URL(String(input)).pathname;
      let body: unknown;
      if (path === "/events")
        body = {
          agents: [
            {
              agent_id: id,
              role: null,
              status: "revoked",
              session_id: null,
              exposures: 1,
            },
            {
              agent_id: live,
              role: null,
              status: "active",
              session_id: null,
              exposures: 0,
            },
          ],
          events: [
            {
              event_id: `evt_${String(1).padStart(26, "0")}`,
              ts: new Date().toISOString(),
              agent_id: id,
              source: "session",
              action: "issued",
              session_id: null,
              secret_ref: null,
              fingerprint: null,
              secret_type: null,
              owner: null,
              owner_agent_id: null,
              target_agent_id: null,
              destination: null,
              encoding: null,
              detail: null,
              tool: null,
            },
          ],
          cursor: null,
        };
      else if (path === "/roles") body = { roles: [] };
      else if (path === "/instances") body = { instances: [] };
      else if (path.endsWith("/ledger"))
        body = {
          agent_id: id,
          entries: [
            {
              secret_ref: null,
              fingerprint: crypto.randomUUID().replaceAll("-", "").repeat(2),
              secret_type: "other",
              owner: "unknown",
              owner_agent_id: null,
              source: "tool_response",
              via: null,
              first_seen: null,
              status: "active",
            },
          ],
        };
      else if (path.endsWith("/blast-radius"))
        body = {
          agent_id: id,
          keys: [{ key_ref: ref, key_hint: null, status: "inactive" }],
          events: [
            {
              ts: new Date().toISOString(),
              key_ref: ref,
              event_name: "ListBuckets",
              event_source: null,
              resource: null,
              source_ip: null,
              error_code: null,
            },
          ],
        };
      else throw new Error("Unexpected endpoint");
      return new Response(JSON.stringify(body));
    });
    vi.stubGlobal("fetch", fetcher);
    await act(async () => {
      render(
        <App
          config={{
            burnUrl: "https://burn.example.test",
            localDev: true,
            demoToken: crypto.randomUUID(),
          }}
        />,
      );
    });
    // The overview roster is the live fleet. The burned agent is not on it,
    // and the running one renders its nullable session.
    expect(screen.getByText("Session or credential issued")).toBeTruthy();
    expect(screen.getByText("No active session")).toBeTruthy();
    expect(screen.getByText("Connected · polling every second")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText("MOCK DATA")).toBeNull();
    // Absent from the roster, though its events stay in the stream.
    const roster = screen.getByRole("region", { name: "Active agents" });
    expect(within(roster).queryByText(id)).toBeNull();
    expect(within(roster).getByText(live)).toBeTruthy();

    // Contained owns a burned agent's ledger and CloudTrail history.
    await act(async () => {
      goTo("burned");
    });
    expect(screen.getAllByText(id).length).toBeGreaterThan(0);
    expect(screen.getByText("Unidentified credential")).toBeTruthy();
    expect(screen.getByText("Source unavailable")).toBeTruthy();
  });

  it("a burned swarm instance leaves the roster at once and stays selected", async () => {
    const scenario = `agent-${crypto.randomUUID()}`;
    const worker = `agent-swarm-${crypto.randomUUID().slice(0, 8)}`;
    const session = `sess_${crypto.randomUUID().slice(0, 8)}`;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = new URL(String(input)).pathname;
      let body: unknown;
      if (path === "/events")
        body = {
          events: [],
          agents: [{ agent_id: scenario, status: "active", session_id: "s" }],
        };
      else if (path === "/instances")
        // Slow to catch up: still reports the worker active after the burn.
        body = {
          instances: [
            {
              session_id: session,
              agent_id: worker,
              role: "swarm",
              status: "active",
              tools: [],
              created_at: "2026-11-08T05:00:00.000Z",
              exposures: 0,
            },
          ],
        };
      else if (path === "/roles") body = { roles: [] };
      else if (path.endsWith("/ledger"))
        body = { agent_id: worker, entries: [] };
      else if (path.endsWith("/blast-radius"))
        body = { agent_id: worker, keys: [], events: [] };
      else if (path === "/burn")
        body = {
          agent_id: worker,
          revoked: true,
          sessions_revoked: [session],
          rotated: [],
          flagged: [],
        };
      else throw new Error(`Unexpected endpoint ${path}`);
      return new Response(JSON.stringify(body));
    });
    vi.stubGlobal("fetch", fetcher);
    await act(async () => {
      render(
        <App
          config={{
            burnUrl: "https://burn.example.test",
            localDev: true,
            demoToken: crypto.randomUUID(),
          }}
        />,
      );
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    fireEvent.click(screen.getByRole("button", { name: `Burn ${worker}` }));
    await act(async () => {
      fireEvent.click(
        within(screen.getByRole("dialog")).getByRole("button", {
          name: "Confirm Burn",
        }),
      );
    });
    const roster = screen.getByRole("region", { name: "Active agents" });
    expect(within(roster).queryByText(worker)).toBeNull();
    expect(screen.getByRole("status", { name: "Burn result" })).toBeTruthy();
    // Several polls later, /instances still says active: nothing moves.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(11_000);
    });
    expect(within(roster).queryByText(worker)).toBeNull();
    expect(screen.getByRole("status", { name: "Burn result" })).toBeTruthy();
    expect(screen.getByText(/CloudTrail event history can lag/)).toBeTruthy();
  });

  it("polls /instances once per interval, and not at all on Alerts", async () => {
    const worker = "agent-worker-a1b2";
    const calls: string[] = [];
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = new URL(String(input)).pathname;
      calls.push(path);
      let body: unknown;
      if (path === "/events") body = { events: [], agents: [] };
      else if (path === "/instances")
        body = {
          instances: [
            {
              session_id: "sess_w",
              agent_id: worker,
              role: "worker",
              status: "active",
              tools: [],
              created_at: "2026-11-08T05:00:00.000Z",
              exposures: 0,
            },
          ],
        };
      else if (path === "/roles") body = { roles: [] };
      else if (path.endsWith("/ledger"))
        body = { agent_id: worker, entries: [] };
      else throw new Error(`Unexpected endpoint ${path}`);
      return new Response(JSON.stringify(body));
    });
    vi.stubGlobal("fetch", fetcher);
    const count = () => calls.filter((path) => path === "/instances").length;
    await act(async () => {
      render(
        <App
          config={{
            burnUrl: "https://burn.example.test",
            localDev: true,
            demoToken: crypto.randomUUID(),
          }}
        />,
      );
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    // One poll for the page: the first call, then one every five seconds.
    expect(count()).toBe(5);

    await act(async () => {
      goTo("alerts");
    });
    const onAlerts = count();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(count()).toBe(onAlerts);

    await act(async () => {
      goTo("overview");
    });
    const roster = screen.getByRole("region", { name: "Active agents" });
    expect(within(roster).getByText(worker)).toBeTruthy();
  });

  it.each(["handoff", "egress"] as const)(
    "keeps blocked %s traffic visible in the live stream",
    async (source) => {
      const id = `agent-${crypto.randomUUID()}`;
      const detail =
        source === "handoff"
          ? "send_message blocked: known credential"
          : "Outbound request blocked: known credential";
      const fetcher = vi
        .fn<typeof fetch>()
        .mockImplementation(async (input) => {
          const path = new URL(String(input)).pathname;
          if (path === "/events")
            return new Response(
              JSON.stringify({
                agents: [{ agent_id: id, status: "active" }],
                events: [
                  {
                    event_id: `evt_${String(1).padStart(26, "0")}`,
                    ts: new Date().toISOString(),
                    agent_id: id,
                    source,
                    action: "blocked",
                    detail,
                  },
                ],
              }),
            );
          if (path === "/roles")
            return new Response(JSON.stringify({ roles: [] }));
          if (path === "/instances")
            return new Response(JSON.stringify({ instances: [] }));
          if (path.endsWith("/ledger"))
            return new Response(JSON.stringify({ agent_id: id, entries: [] }));
          throw new Error("Unexpected endpoint");
        });
      vi.stubGlobal("fetch", fetcher);
      await act(async () => {
        render(
          <App
            config={{
              burnUrl: "https://burn.example.test",
              localDev: true,
              demoToken: crypto.randomUUID(),
            }}
          />,
        );
      });
      const feed = screen.getByRole("region", { name: "Live events" });
      expect(within(feed).getByText("Outbound leak blocked")).toBeTruthy();
      expect(within(feed).getByText(detail)).toBeTruthy();
      expect(feed.textContent).toContain(source);
      expect(
        fetcher.mock.calls.every(([url]) =>
          ["/events", "/roles", "/instances", `/agents/${id}/ledger`].includes(
            new URL(String(url)).pathname,
          ),
        ),
      ).toBe(true);
    },
  );
});

it("opens replacement links and quarantined details from the one shared instance poll", async () => {
  const old = {
    session_id: "session_before",
    agent_id: "agent-worker-112233",
    role: "worker",
    status: "revoked",
    created_at: "2026-11-08T04:00:00Z",
    replaced_by: "session_after",
    items_started: 3,
    replacement: {
      status: "linked",
      cutoff: "2026-11-08T05:00:00Z",
      cutoff_source: "evidence",
      done: ["item-1", "item-2"],
      quarantined: ["item-review"],
    },
  };
  const replacement = {
    session_id: "session_after",
    agent_id: "agent-worker-445566",
    role: "worker",
    status: "active",
    created_at: "2026-11-08T05:01:00Z",
    replaces: old.session_id,
    generation: 1,
  };
  const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const path = new URL(String(input)).pathname;
    const data =
      path === "/instances"
        ? { instances: [replacement, old] }
        : path === "/roles"
          ? { roles: [{ name: "worker", managed_by: "ui", tools: [] }] }
          : path.endsWith("/ledger")
            ? { agent_id: replacement.agent_id, entries: [] }
            : { events: [], agents: [] };
    return new Response(JSON.stringify(data));
  });
  vi.stubGlobal("fetch", fetcher);
  await act(async () => {
    render(
      <App
        config={{
          burnUrl: "http://burn.example.test",
          demoToken: crypto.randomUUID(),
        }}
      />,
    );
  });
  const count = () =>
    fetcher.mock.calls.filter(
      ([url]) => new URL(String(url)).pathname === "/instances",
    ).length;
  expect(count()).toBe(1);
  const previousLink = screen.getByRole("link", { name: old.agent_id });
  await act(async () => {
    window.location.hash = previousLink.getAttribute("href")!;
    window.dispatchEvent(new Event("hashchange"));
  });
  let dialog = screen.getByRole("dialog");
  expect(
    within(dialog).getByRole("heading", { name: old.agent_id }),
  ).toBeTruthy();
  expect(within(dialog).getByText("item-review")).toBeTruthy();
  const nextLink = within(dialog).getByRole("link", {
    name: replacement.agent_id,
  });
  await act(async () => {
    window.location.hash = nextLink.getAttribute("href")!;
    window.dispatchEvent(new Event("hashchange"));
  });
  dialog = screen.getByRole("dialog");
  expect(
    within(dialog).getByRole("heading", { name: replacement.agent_id }),
  ).toBeTruthy();
  expect(within(dialog).getByText("item-review")).toBeTruthy();
  expect(count()).toBe(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5_000);
  });
  expect(count()).toBe(2);
  expect(
    fetcher.mock.calls.some(([url]) =>
      new URL(String(url)).pathname.startsWith("/instances/"),
    ),
  ).toBe(false);
});
