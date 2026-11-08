// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
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

/** The board's state: worker-07 burned by a person, one shared key open. */
function boardState(flags = [flag({ flag_level: "reported" })]) {
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
  });
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

  it("shows the board: the hero's counts, the burned agents and what is still open", () => {
    show(boardState());
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "1 agent burned today.1 key waits on its owner.",
    );
    const list = screen.getByRole("region", { name: /Burned agents/i });
    const rows = within(list).getAllByRole("link");
    expect(rows).toHaveLength(2);
    const worker = rows[0];
    expect(worker.getAttribute("href")).toBe("#/agents/s7");
    expect(worker.textContent).toContain("worker-07");
    expect(worker.textContent).toContain(
      "Burned by priya.nair@example.com: “read an unverified vendor page”",
    );
    expect(worker.textContent).toContain(
      "Badge revoked, 2 owned keys rotated, crm_db_url sent to its owner",
    );
    expect(within(worker).getByText("Key open")).toBeTruthy();
    // The reader's burn is older than the events, so it says less.
    expect(rows[1].textContent).toContain("reader-01");
    expect(rows[1].textContent).toContain("Burned before the events loaded");
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
    expect(how.textContent).toContain("what started 1 other burn");
  });

  it("filters to open and burned incidents with counts that match the rows", () => {
    show(boardState());
    const filter = screen.getByRole("group", { name: "Show" });
    expect(
      within(filter)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["All 2", "Open 1", "Burned 1"]);
    const list = screen.getByRole("region", { name: /Burned agents/i });

    fireEvent.click(within(filter).getByRole("button", { name: "Open 1" }));
    expect(within(list).getAllByRole("link")).toHaveLength(1);
    expect(within(list).getByRole("link").textContent).toContain("worker-07");
    expect(window.location.hash).toBe("#/incidents?show=open");

    fireEvent.click(within(filter).getByRole("button", { name: "Burned 1" }));
    expect(within(list).getByRole("link").textContent).toContain("reader-01");
    expect(window.location.hash).toBe("#/incidents?show=burned");

    fireEvent.click(within(filter).getByRole("button", { name: "All 2" }));
    expect(window.location.hash).toBe("#/incidents");
  });

  it("closes an incident once its key is decided", () => {
    show(boardState([flag({ state: "dismissed" })]));
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
