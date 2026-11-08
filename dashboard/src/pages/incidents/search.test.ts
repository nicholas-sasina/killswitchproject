import { describe, expect, it } from "vitest";
import {
  consoleValue,
  event,
  flag,
  instance,
  today,
} from "../../features/flags/testConsole";
import { inTab, matches, searchTerms, searchText, tabCounts } from "./search";

const SHARED = "burn/shared/crm_db_url";

function incidents() {
  return consoleValue({
    instances: [
      instance("s7", "revoked", { created_at: today(1, 0) }),
      instance("r1", "revoked", {
        agent_id: "agent-reader-r1",
        role: "reader",
        created_at: today(0, 30),
      }),
    ],
    events: [
      event({
        action: "flagged",
        agent_id: "agent-worker-s7",
        secret_ref: SHARED,
      }),
    ],
    flags: [flag()],
  }).incidents;
}

/** The one worker (worker-01, with the open key) and the reader. */
function pair() {
  const all = incidents();
  const by = (session: string) =>
    all.find((incident) => incident.agent.sessionId === session)!;
  return [by("s7"), by("r1")] as const;
}

describe("searching incidents", () => {
  it("splits a query into lower-case words", () => {
    expect(searchTerms(undefined)).toEqual([]);
    expect(searchTerms("   ")).toEqual([]);
    expect(searchTerms("  Worker  CRM_db ")).toEqual(["worker", "crm_db"]);
  });

  it("finds a row by name, role, key, why and status, ignoring case", () => {
    const [worker, reader] = pair();
    const text = searchText({
      incident: worker,
      why: "Burned by priya.nair@example.com: read an unverified vendor page",
      status: "Key open",
    });
    for (const query of [
      "worker-01",
      "WORKER",
      "crm_db_url",
      "burn/shared/crm",
      "priya",
      "vendor page",
      "key open",
      "agent-worker-s7",
    ])
      expect(matches(text, searchTerms(query)), query).toBe(true);
    // Every word must appear, in any order.
    expect(matches(text, searchTerms("open worker"))).toBe(true);
    expect(matches(text, searchTerms("worker reader"))).toBe(false);

    const other = searchText({
      incident: reader,
      why: "No reason was recorded",
      status: "Burned",
    });
    expect(matches(other, searchTerms("reader-01"))).toBe(true);
    expect(matches(other, searchTerms("crm_db_url"))).toBe(false);
  });

  it("counts the tabs from the rows the search kept", () => {
    const [worker, reader] = pair();
    expect(worker.open).toBe(true);
    expect(reader.open).toBe(false);
    const rows = [{ incident: worker }, { incident: reader }];
    expect(tabCounts(rows)).toEqual({ all: 2, open: 1, burned: 1 });
    expect(tabCounts(rows.slice(1))).toEqual({ all: 1, open: 0, burned: 1 });
    expect(inTab(worker, "open")).toBe(true);
    expect(inTab(worker, "burned")).toBe(false);
    expect(inTab(reader, "all")).toBe(true);
  });
});
