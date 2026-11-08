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
import type { LedgerResponse } from "../../contracts";
import type { ConsoleData } from "../../data/ConsoleData";
import {
  consoleValue,
  event,
  flag,
  instance,
  today,
  WithConsole,
} from "../../features/flags/testConsole";
import { IncidentsPage } from "./IncidentsPage";

beforeEach(() => window.history.replaceState(null, "", "/#/incidents"));
afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

function show(value: ConsoleData) {
  return render(
    <WithConsole value={value}>
      <IncidentsPage />
    </WithConsole>,
  );
}

const SHARED = "burn/shared/crm_db_url";

/** worker-07 burned by a person with one shared key open; reader-01 burned before the loaded events. */
function burnedWithOpenKey(
  flags = [flag({ flag_level: "reported" })],
  api: Partial<BurnApi> = {},
) {
  const workers = Array.from({ length: 8 }, (_, index) =>
    instance(`s${index + 1}`, index === 6 ? "revoked" : "active", {
      created_at: today(1, index),
    }),
  );
  return consoleValue({
    instances: [
      ...workers,
      instance("r1", "revoked", {
        agent_id: "agent-reader-r1",
        role: "reader",
        created_at: today(0, 30),
      }),
    ],
    events: [
      event({
        action: "recorded",
        source: "tool_response",
        agent_id: "agent-worker-s8",
        secret_ref: SHARED,
      }),
      event({
        action: "revoked",
        agent_id: "agent-worker-s7",
        session_id: "s7",
        ts: today(19, 42),
        actor: "priya.nair@example.com",
        detail: "1 session(s) revoked (read an unverified vendor page)",
      }),
      event({
        action: "rotated",
        agent_id: "agent-worker-s7",
        ts: today(19, 42),
        secret_ref: "burn/agent-worker-s7/api_key",
      }),
      event({
        action: "rotated",
        agent_id: "agent-worker-s7",
        ts: today(19, 42),
        secret_ref: "burn/agent-worker-s7/aws_key",
      }),
      event({
        action: "flagged",
        agent_id: "agent-worker-s7",
        ts: today(19, 42),
        secret_ref: SHARED,
      }),
    ],
    flags,
    api,
  });
}

/** The rows of the Burned agents list: each is the button that opens it. */
function rowsIn(list: HTMLElement): HTMLElement[] {
  return within(list)
    .getAllByRole("button")
    .filter((button) => button.hasAttribute("aria-expanded"));
}

describe("Incidents", () => {
  it("waits for the data instead of claiming nothing burned", () => {
    show(
      consoleValue({
        instancesLoaded: false,
        flagsLoaded: false,
      }),
    );
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "Incidents",
    );
    expect(screen.getByText("Loading burned agents…")).toBeTruthy();
    expect(screen.getByText("Loading flagged keys…")).toBeTruthy();
  });

  it("says what will appear when nothing has been burned", () => {
    show(consoleValue({ instances: [instance("s1", "active")] }));
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "No agent burned today.No key waits on its owner.",
    );
    expect(screen.getByText(/No agent has been burned\./)).toBeTruthy();
    expect(screen.getByText(/Nothing is open\./)).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Show" })).toBeNull();
    expect(
      screen.queryByRole("heading", { name: /How burns started/i }),
    ).toBeNull();
  });

  it("shows the hero's counts, the burned agents and what is still open", () => {
    show(burnedWithOpenKey());
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "1 agent burned today.1 key waits on its owner.",
    );
    const list = screen.getByRole("region", { name: /Burned agents/i });
    const rows = rowsIn(list);
    expect(rows).toHaveLength(2);
    const worker = rows[0];
    expect(worker.getAttribute("aria-expanded")).toBe("false");
    expect(worker.textContent).toContain("worker-07");
    expect(worker.textContent).toContain(
      "Burned by priya.nair@example.com: read an unverified vendor page",
    );
    expect(worker.textContent).toContain(
      "Badge revoked, 2 owned keys rotated, crm_db_url sent to its owner",
    );
    expect(within(worker).getByText("Key open")).toBeTruthy();
    // The reader's burn is older than the events, so it says less.
    expect(rows[1].textContent).toContain("reader-01");
    expect(rows[1].textContent).toContain("Burned before the loaded events");
    expect(within(rows[1]).getByText("Burned")).toBeTruthy();

    const open = screen.getByRole("region", { name: /Still open/i });
    expect(open.textContent).toContain("Shared key / reported");
    expect(open.textContent).toContain("From the worker-07 incident.");
    expect(open.textContent).toContain(
      "worker-08, still running, also saw it.",
    );
    expect(
      within(open).getByRole("button", { name: "Rotate crm_db_url" }),
    ).toBeTruthy();

    const how = screen.getByRole("region", { name: /How burns started/i });
    expect(how.textContent).toContain("A person pressed Burn1");
    expect(how.textContent).not.toContain("Canary");
    expect(how.textContent).toContain(
      "For 1 other burn, the loaded events don’t name who or what started it.",
    );
  });

  it("filters to open and burned incidents with counts that match the rows", () => {
    show(burnedWithOpenKey());
    const filter = screen.getByRole("group", { name: "Show" });
    expect(
      within(filter)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["All 2", "Open 1", "Burned 1"]);
    const list = screen.getByRole("region", { name: /Burned agents/i });

    fireEvent.click(within(filter).getByRole("button", { name: "Open 1" }));
    expect(rowsIn(list)).toHaveLength(1);
    expect(rowsIn(list)[0].textContent).toContain("worker-07");
    expect(window.location.hash).toBe("#/incidents?show=open");

    fireEvent.click(within(filter).getByRole("button", { name: "Burned 1" }));
    expect(rowsIn(list)[0].textContent).toContain("reader-01");
    expect(window.location.hash).toBe("#/incidents?show=burned");

    fireEvent.click(within(filter).getByRole("button", { name: "All 2" }));
    expect(window.location.hash).toBe("#/incidents");
  });

  it("closes an incident once its key is decided", () => {
    show(burnedWithOpenKey([flag({ state: "dismissed" })]));
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "1 agent burned today.No key waits on its owner.",
    );
    const list = screen.getByRole("region", { name: /Burned agents/i });
    expect(within(list).queryByText("Key open")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open 0" }));
    expect(within(list).getByText(/Nothing is open\./)).toBeTruthy();
  });

  it("explains a flags failure inside its card", () => {
    show(
      consoleValue({
        flagsLoaded: false,
        flagsError: "API request failed (HTTP 500).",
      }),
    );
    const open = screen.getByRole("region", { name: /Still open/i });
    expect(within(open).getByRole("alert").textContent).toBe(
      "Flagged keys could not be loaded: API request failed (HTTP 500). Burn retries every 5 seconds.",
    );
  });
});

/** What worker-07's ledger says: the shared key in plain text, its own key by reference. */
function ledgerOf(agentId: string): LedgerResponse {
  return {
    agent_id: agentId,
    entries: [
      {
        secret_ref: SHARED,
        fingerprint: "f1",
        secret_type: "other",
        owner: "shared",
        source: "tool_response",
        source_ref: "read_config:crm",
        first_seen: today(1, 5),
        status: "flagged",
        exposure: "seen",
      },
      {
        secret_ref: `burn/${agentId}/api_key`,
        fingerprint: "f2",
        secret_type: "api_key",
        owner: "agent",
        owner_agent_id: agentId,
        source: "tool_response",
        first_seen: today(1, 6),
        status: "rotated",
        exposure: "referenced",
      },
    ],
  };
}

describe("Incidents: search and rows that open", () => {
  it("keeps the query in the hash and reads it back", () => {
    window.history.replaceState(null, "", "/#/incidents?show=burned&q=reader");
    show(burnedWithOpenKey());
    const box = screen.getByRole("searchbox", { name: "Search incidents" });
    expect((box as HTMLInputElement).value).toBe("reader");
    const list = screen.getByRole("region", { name: /Burned agents/i });
    expect(rowsIn(list).map((row) => row.textContent)).toEqual([
      expect.stringContaining("reader-01"),
    ]);

    fireEvent.change(box, { target: { value: "Worker" } });
    expect(window.location.hash).toBe("#/incidents?show=burned&q=Worker");
    // worker-07 matches but is open, so Burned shows none of them.
    expect(
      within(list).getByText("No burned incidents match “Worker”."),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /^All 1/ }));
    expect(window.location.hash).toBe("#/incidents?q=Worker");
    expect(rowsIn(list)).toHaveLength(1);

    fireEvent.change(box, { target: { value: "" } });
    expect(window.location.hash).toBe("#/incidents");
    expect(window.location.search).toBe("");
    expect(rowsIn(list)).toHaveLength(2);
  });

  it("says when nothing matches, and Clear brings every row back", () => {
    window.history.replaceState(null, "", "/#/incidents?q=nothing-like-this");
    show(burnedWithOpenKey());
    const list = screen.getByRole("region", { name: /Burned agents/i });
    expect(
      within(list).getByText("No incidents match “nothing-like-this”."),
    ).toBeTruthy();
    fireEvent.click(within(list).getByRole("button", { name: "Clear" }));
    expect(window.location.hash).toBe("#/incidents");
    expect(rowsIn(list)).toHaveLength(2);
    expect(document.activeElement).toBe(
      screen.getByRole("searchbox", { name: "Search incidents" }),
    );
  });

  it("keeps every count in step with the tabs and the navigation", () => {
    const value = burnedWithOpenKey();
    show(value);
    const tabs = () =>
      within(screen.getByRole("group", { name: "Show" }))
        .getAllByRole("button")
        .map((button) => button.textContent);
    // No search: the tabs are the console's own counts, as in the navigation.
    const { all, open, closed } = value.incidentCounts;
    expect(tabs()).toEqual([`All ${all}`, `Open ${open}`, `Burned ${closed}`]);

    fireEvent.change(screen.getByRole("searchbox"), {
      target: { value: "crm_db_url" },
    });
    const list = screen.getByRole("region", { name: /Burned agents/i });
    expect(tabs()).toEqual(["All 1", "Open 1", "Burned 0"]);
    expect(rowsIn(list)).toHaveLength(1);
    expect(list.querySelector("h2")?.textContent).toContain("1 of 2 match");
    // The navigation still counts every open incident, search or not.
    expect(value.incidentCounts.open).toBe(1);
  });

  it("puts the cursor in the search box on '/', unless it's already in a field", () => {
    show(burnedWithOpenKey());
    const box = screen.getByRole("searchbox", { name: "Search incidents" });
    fireEvent.keyDown(document.body, { key: "/" });
    expect(document.activeElement).toBe(box);

    const other = document.createElement("textarea");
    document.body.append(other);
    other.focus();
    fireEvent.keyDown(other, { key: "/" });
    expect(document.activeElement).toBe(other);
    other.remove();
  });

  it("opens a row in place and asks for its ledger once", async () => {
    const ledger = vi.fn((agentId: string) =>
      Promise.resolve(ledgerOf(agentId)),
    );
    show(burnedWithOpenKey(undefined, { ledger }));
    const list = screen.getByRole("region", { name: /Burned agents/i });
    const [worker] = rowsIn(list);
    const panel = document.getElementById(
      worker.getAttribute("aria-controls")!,
    )!;
    expect(panel.hidden).toBe(true);

    fireEvent.click(worker);
    expect(worker.getAttribute("aria-expanded")).toBe("true");
    expect(panel.hidden).toBe(false);
    expect(within(panel).getByText("Loading what worker-07 saw…")).toBeTruthy();
    await act(async () => {});
    expect(ledger).toHaveBeenCalledTimes(1);
    expect(ledger.mock.calls[0][0]).toBe("agent-worker-s7");

    const saw = within(panel)
      .getByRole("heading", { name: "What it saw" })
      .closest("section")!;
    expect(within(saw).getByText("crm_db_url").className).toContain("id");
    expect(within(saw).getByText("Plain text").className).toContain("is-hot");
    const did = within(panel)
      .getByRole("heading", { name: "What Burn did" })
      .closest("section")!;
    expect(did.textContent).toContain("Badge revoked");
    expect(did.textContent).toContain(
      "crm_db_url flagged for its owner, still open",
    );
    const stillOpen = within(panel)
      .getByRole("heading", { name: "Still open" })
      .closest("section")!;
    expect(
      within(stillOpen).getByRole("button", { name: "Rotate crm_db_url" }),
    ).toBeTruthy();
    expect(
      within(panel)
        .getByRole("link", { name: "Open worker-07" })
        .getAttribute("href"),
    ).toBe("#/agents/s7");

    // Shut and open again: the ledger is kept, not asked for again.
    fireEvent.click(worker);
    expect(panel.hidden).toBe(true);
    fireEvent.click(worker);
    await act(async () => {});
    expect(ledger).toHaveBeenCalledTimes(1);
    expect(within(panel).getByText("Plain text")).toBeTruthy();
  });

  it("says plainly when a ledger fails, and tries again on request", async () => {
    const ledger = vi
      .fn<(agentId: string) => Promise<LedgerResponse>>()
      .mockRejectedValueOnce(new Error("API request failed (HTTP 503)."))
      .mockImplementation((agentId) => Promise.resolve(ledgerOf(agentId)));
    show(burnedWithOpenKey(undefined, { ledger }));
    const list = screen.getByRole("region", { name: /Burned agents/i });
    fireEvent.click(rowsIn(list)[0]);
    await act(async () => {});
    const alert = within(list).getByRole("alert");
    expect(alert.textContent).toBe(
      "Its ledger could not be loaded: API request failed (HTTP 503).",
    );
    expect(alert.className).not.toMatch(/ember/);
    fireEvent.click(within(list).getByRole("button", { name: "Try again" }));
    await act(async () => {});
    expect(ledger).toHaveBeenCalledTimes(2);
    expect(within(list).getByText("Plain text")).toBeTruthy();
  });

  it("expands and collapses every row, asking for ledgers four at a time", async () => {
    const pending: Array<() => void> = [];
    const ledger = vi.fn(
      (agentId: string) =>
        new Promise<LedgerResponse>((resolve) =>
          pending.push(() => resolve(ledgerOf(agentId))),
        ),
    );
    const value = consoleValue({
      instances: Array.from({ length: 6 }, (_, index) =>
        instance(`s${index + 1}`, "revoked", { created_at: today(1, index) }),
      ),
      api: { ledger },
    });
    show(value);
    const list = screen.getByRole("region", { name: /Burned agents/i });
    expect(rowsIn(list)).toHaveLength(6);
    const collapse = within(list).getByRole("button", { name: "Collapse all" });
    expect((collapse as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(within(list).getByRole("button", { name: "Expand all" }));
    expect(
      rowsIn(list).every((row) => row.getAttribute("aria-expanded") === "true"),
    ).toBe(true);
    await act(async () => {});
    expect(ledger).toHaveBeenCalledTimes(4);

    await act(async () => pending.shift()!());
    expect(ledger).toHaveBeenCalledTimes(5);
    await act(async () => {
      while (pending.length) pending.shift()!();
    });
    await act(async () => {
      while (pending.length) pending.shift()!();
    });
    expect(ledger).toHaveBeenCalledTimes(6);
    expect(new Set(ledger.mock.calls.map(([id]) => id)).size).toBe(6);

    fireEvent.click(collapse);
    expect(
      rowsIn(list).every(
        (row) => row.getAttribute("aria-expanded") === "false",
      ),
    ).toBe(true);
    // Opening them all again uses the ledgers already loaded.
    fireEvent.click(within(list).getByRole("button", { name: "Expand all" }));
    await act(async () => {});
    expect(ledger).toHaveBeenCalledTimes(6);
  });
});
