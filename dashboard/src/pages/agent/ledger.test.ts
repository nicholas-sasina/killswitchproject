import { describe, expect, it } from "vitest";
import type { LedgerEntry } from "../../contracts";
import { event } from "../../features/burn/fixture";
import { evidenceRefs, ledgerCounts, ledgerRows } from "./ledger";

const ME = "agent-worker-7";

function entry(over: Partial<LedgerEntry>): LedgerEntry {
  return {
    secret_ref: null,
    fingerprint: "fp00112233445566",
    secret_type: "other",
    owner: "shared",
    source: "tool_response",
    first_seen: "2026-11-08T19:12:00Z",
    status: "active",
    exposure: "seen",
    ...over,
  };
}

const canary = entry({
  secret_ref: `burn/${ME}/canary_9f2e`,
  owner: "agent",
  owner_agent_id: ME,
  source_ref: "read_ticket:#4821",
  first_seen: "2026-11-08T19:31:12Z",
});
const crm = entry({
  secret_ref: "burn/shared/crm_db_url",
  source_ref: "read_config:crm.yaml",
  via: "read_config",
  first_seen: "2026-11-08T19:41:02Z",
});
const apiKey = entry({
  secret_ref: `burn/${ME}/api_key`,
  secret_type: "api_key",
  owner: "agent",
  owner_agent_id: ME,
  exposure: "referenced",
  first_seen: "2026-11-08T19:12:05Z",
});
const awsKey = entry({
  secret_ref: `burn/${ME}/aws_key`,
  secret_type: "aws_key",
  owner: "agent",
  owner_agent_id: ME,
  exposure: "referenced",
  first_seen: "2026-11-08T19:12:06Z",
});
const board = [awsKey, crm, apiKey, canary];

describe("ledgerCounts", () => {
  it("counts every key seen, the plain text ones but its canary, and its own", () => {
    expect(ledgerCounts(board, ME)).toEqual({ seen: 4, plain: 1, owned: 2 });
  });

  it("is all zero for an empty ledger", () => {
    expect(ledgerCounts([], ME)).toEqual({ seen: 0, plain: 0, owned: 0 });
  });
});

describe("evidenceRefs", () => {
  it("is the keys it saw in plain text, but its own keys and its canary", () => {
    expect([...evidenceRefs(board, ME)]).toEqual(["burn/shared/crm_db_url"]);
    expect(evidenceRefs([{ ...crm, exposure: "referenced" }], ME).size).toBe(0);
    expect(evidenceRefs(undefined, ME).size).toBe(0);
  });
});

describe("ledgerRows", () => {
  it("draws the running board: canary first, the evidence, then its own keys", () => {
    const blocked = event({
      action: "blocked",
      agent_id: ME,
      source: "egress",
      secret_ref: "burn/shared/crm_db_url",
    });
    const rows = ledgerRows(board, {
      agentId: ME,
      events: [blocked],
      burned: false,
    });
    expect(
      rows.map((row) => [
        row.name,
        row.source,
        row.evidence,
        row.action.text,
        row.dot,
      ]),
    ).toEqual([
      [
        "session canary",
        "its decoy key, planted in #4821",
        "Exposed, never used",
        "Watching",
        "ember",
      ],
      [
        "crm_db_url",
        "read_config crm.yaml, shared",
        "Plain text; leak blocked at exit",
        "Tracked",
        "ember",
      ],
      ["api_key", "its own copy", "Seen as a reference", "Tracked", "faint"],
      ["aws_key", "its own copy", "Seen as a reference", "Tracked", "faint"],
    ]);
    expect(rows[1].hot).toBe(true);
    expect(rows[2].hot).toBe(false);
  });

  it("draws the burned board: canary used, keys rotated, the shared key for review", () => {
    const rows = ledgerRows(
      [
        { ...awsKey, status: "rotated" },
        { ...crm, status: "flagged" },
        { ...apiKey, status: "rotated" },
        canary,
      ],
      {
        agentId: ME,
        events: [
          event({
            action: "rejected",
            agent_id: ME,
            secret_ref: `burn/${ME}/api_key`,
          }),
          event({
            action: "canary_used",
            agent_id: ME,
            secret_ref: `burn/${ME}/canary_9f2e`,
            detail: "canary presented (from 203.0.113.9)",
          }),
        ],
        burned: true,
      },
    );
    expect(
      rows.map((row) => [row.name, row.evidence, row.action, row.dot]),
    ).toEqual([
      [
        "session canary",
        "Used outside Burn from 203.0.113.9",
        { text: "Revoked", tone: "done" },
        "ember",
      ],
      [
        "crm_db_url",
        "Plain text",
        { text: "Owner review", tone: "flag" },
        "ember",
      ],
      [
        "api_key",
        "Old value refused",
        { text: "Rotated", tone: "done" },
        "ink",
      ],
      ["aws_key", "Old key inactive", { text: "Rotated", tone: "done" }, "ink"],
    ]);
  });

  it("says a running agent's flagged key is flagged, not under review", () => {
    const [row] = ledgerRows([{ ...crm, status: "flagged" }], {
      agentId: ME,
      events: [],
      burned: false,
    });
    expect(row.action).toEqual({ text: "Flagged", tone: "flag" });
  });

  it("names a key that belongs to another agent and who handed it over", () => {
    const [row] = ledgerRows(
      [
        entry({
          secret_ref: "burn/agent-analyst-1/api_key",
          owner: "agent",
          owner_agent_id: "agent-analyst-1",
          source: "handoff",
          via: "agent-analyst-1",
        }),
      ],
      {
        agentId: ME,
        events: [],
        burned: false,
        nameOf: (id) => (id === "agent-analyst-1" ? "analyst-01" : undefined),
      },
    );
    expect(row.name).toBe("agent-analyst-1/api_key");
    expect(row.source).toBe("handed over by analyst-01, analyst-01's own key");
    expect(row.evidence).toBe("Plain text");
  });

  it("keeps an unrecognised key by its fingerprint", () => {
    const [row] = ledgerRows([entry({ owner: "unknown" })], {
      agentId: ME,
      events: [],
      burned: false,
    });
    expect(row).toMatchObject({
      name: "Unrecognised key",
      mono: false,
      source: "fingerprint fp001122",
    });
  });

  it("puts shared keys seen only as references after its own keys", () => {
    const rows = ledgerRows(
      [{ ...crm, exposure: "referenced" }, apiKey, awsKey],
      { agentId: ME, events: [], burned: false },
    );
    expect(rows.map((row) => row.name)).toEqual([
      "api_key",
      "aws_key",
      "crm_db_url",
    ]);
  });
});
