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
import { CredentialsPage } from "./CredentialsPage";

beforeEach(() => window.history.replaceState(null, "", "/#/credentials"));
afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "/");
});

function show(value: ConsoleData) {
  return render(
    <WithConsole value={value}>
      <CredentialsPage />
    </WithConsole>,
  );
}

const SHARED = "burn/shared/crm_db_url";

function fleet() {
  return consoleValue({
    instances: [
      instance("s1", "revoked", { created_at: today(1, 0) }),
      instance("s2", "active", { created_at: today(2, 0) }),
      instance("a1", "active", {
        agent_id: "agent-analyst-a1",
        role: "analyst",
        created_at: today(3, 0),
      }),
    ],
    events: [
      event({
        action: "recorded",
        agent_id: "agent-worker-s1",
        secret_ref: SHARED,
      }),
      event({
        action: "recorded",
        agent_id: "agent-worker-s2",
        secret_ref: SHARED,
      }),
      event({
        action: "rotated",
        agent_id: "system",
        secret_ref: "burn/shared/billing_token",
        ts: today(9, 0),
        actor: "priya.nair@example.com",
        detail:
          "priya.nair@example.com rotated this flagged credential (was seen, 1 instance(s)): leaked in a ticket",
      }),
    ],
    flags: [
      flag({
        flag_level: "reported",
        instance_count: 2,
        evidence: {
          seen: { source: "burn", agent_id: "agent-worker-s1" },
          reported: { source: "provider" },
        },
      }),
      flag({
        secret_ref: "burn/agent-analyst-a1/api_key",
        owner_agent_id: "agent-analyst-a1",
        state: "dismissed",
        resolution: {
          action: "dismiss",
          reason: "test fixture key",
          at: today(10, 0),
          level: "seen",
        },
      }),
    ],
  });
}

describe("Credentials", () => {
  it("waits for /flags before counting keys", () => {
    show(consoleValue({ flagsLoaded: false }));
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "Credentials",
    );
    expect(screen.getByText("Loading flagged keys…")).toBeTruthy();
  });

  it("says what will appear when nothing is flagged", () => {
    show(consoleValue());
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "No key waits on its owner.",
    );
    expect(screen.getByText(/No flagged keys\./)).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("lists open keys with their owner, level, who saw them and the first evidence", () => {
    show(fleet());
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "1 key waits on its owner.",
    );
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows).toHaveLength(2); // the header and one open key
    const key = rows[1];
    const cells = within(key).getAllByRole("cell");
    expect(cells[0].textContent).toBe("crm_db_urlburn/shared/crm_db_url");
    expect(cells[1].textContent).toBe("Shared");
    expect(cells[2].textContent).toBe("Reported");
    expect(
      within(cells[3])
        .getAllByRole("link")
        .map((link) => [link.textContent, link.getAttribute("href")]),
    ).toEqual([
      ["worker-01", "#/agents/s1"],
      ["worker-02", "#/agents/s2"],
    ]);
    expect(cells[3].textContent).toContain("Evidence from 2 agents");
    expect(cells[4].textContent).toContain("worker-01 was burned holding it");
    expect(
      within(cells[5]).getByRole("button", { name: "Rotate crm_db_url" }),
    ).toBeTruthy();
  });

  it("keeps decided keys under Resolved, rotated ones included", () => {
    show(fleet());
    const filter = screen.getByRole("group", { name: "Show" });
    expect(
      within(filter)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["Open 1", "Resolved 2", "All 3"]);
    fireEvent.click(within(filter).getByRole("button", { name: "Resolved 2" }));
    expect(window.location.hash).toBe("#/credentials?show=resolved");
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("Key"),
      expect.stringContaining("Dismissed by its owner“test fixture key”"),
      expect.stringContaining("Rotated by priya.nair@example.com"),
    ]);
    // Another agent's key names its owner.
    expect(
      within(rows[1]).getByRole("link", { name: "analyst-01" }),
    ).toBeTruthy();
    expect(within(rows[1]).queryByRole("button")).toBeNull();
  });

  it("explains a flags failure inside the card", () => {
    show(
      consoleValue({
        flagsLoaded: false,
        flagsError: "API request failed (HTTP 503).",
      }),
    );
    expect(screen.getByRole("alert").textContent).toBe(
      "Flagged keys could not be loaded: API request failed (HTTP 503). Burn retries every 5 seconds.",
    );
  });
});
