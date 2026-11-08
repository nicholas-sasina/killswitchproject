import { describe, expect, it } from "vitest";
import type { BurnResult, LedgerEntry } from "../../contracts";
import { makeConsole } from "./fixture";
import {
  agentPlan,
  containmentSteps,
  credentialsFor,
  keyKind,
  listWords,
  ownedKeys,
  plainSharedKeys,
  planText,
  rolePlan,
} from "./steps";
import type { BurnOutcome } from "./types";

function entry(over: Partial<LedgerEntry>): LedgerEntry {
  return {
    secret_ref: "burn/shared/crm_db_url",
    fingerprint: "f",
    secret_type: "other",
    owner: "shared",
    source: "tool_response",
    first_seen: "2026-11-08T19:00:00Z",
    status: "active",
    ...over,
  };
}

const ME = "agent-worker-3";
const LEDGER: LedgerEntry[] = [
  entry({
    secret_ref: `burn/${ME}/canary_ab12`,
    owner: "agent",
    owner_agent_id: ME,
  }),
  entry({ secret_ref: "burn/shared/crm_db_url", exposure: "seen" }),
  entry({ secret_ref: "burn/shared/billing_token", exposure: "referenced" }),
  entry({
    secret_ref: `burn/${ME}/api_key`,
    secret_type: "api_key",
    owner: "agent",
    owner_agent_id: ME,
    exposure: "referenced",
  }),
  entry({
    secret_ref: "burn/agent-analyst-1/api_key",
    secret_type: "api_key",
    owner: "agent",
    owner_agent_id: "agent-analyst-1",
  }),
];

function result(over: Partial<BurnResult> = {}): BurnResult {
  return {
    agent_id: ME,
    revoked: true,
    sessions_revoked: ["sess-w3"],
    rotated: [`burn/${ME}/api_key`, `burn/${ME}/aws_key`],
    flagged: ["burn/shared/crm_db_url"],
    ...over,
  };
}

describe("keys", () => {
  it("tells its own keys from shared ones, another agent's and its canary", () => {
    expect(LEDGER.map((item) => keyKind(item, ME))).toEqual([
      "canary",
      "shared",
      "shared",
      "owned",
      "shared",
    ]);
    expect(keyKind(entry({ owner: "unknown" }), ME)).toBe("unknown");
  });

  it("rotates its own keys, from the ledger and its role's credentials", () => {
    expect(ownedKeys(LEDGER, ME)).toEqual(["api_key"]);
    expect(ownedKeys(LEDGER, ME, ["aws_key", "api_key"])).toEqual([
      "api_key",
      "aws_key",
    ]);
  });

  it("flags only keys it doesn't own whose plain text reached it, never a canary", () => {
    expect(plainSharedKeys(LEDGER, ME)).toEqual([
      "agent-analyst-1/api_key",
      "crm_db_url",
    ]);
  });

  it("narrows a child's credentials to its parent's", () => {
    const data = makeConsole();
    const child = data.model.byKey.get("sess-r1")!;
    expect(credentialsFor(child, data.model)).toEqual(["api_key"]);
    expect(
      credentialsFor(data.model.byKey.get("sess-w2")!, data.model),
    ).toEqual(["api_key", "aws_key"]);
  });

  it("joins words as a sentence does", () => {
    expect(listWords([])).toBe("");
    expect(listWords(["a"])).toBe("a");
    expect(listWords(["a", "b"])).toBe("a and b");
    expect(listWords(["a", "b", "c"])).toBe("a, b and c");
  });
});

describe("agentPlan", () => {
  const agent = { agentId: ME, role: "worker", sessionId: "sess-w3" };

  it("names its owned keys, the shared keys it saw in plain text, and its children", () => {
    expect(
      planText(
        agentPlan({
          agent,
          entries: LEDGER,
          credentials: ["api_key", "aws_key"],
          childrenRunning: ["reader-01"],
          replaceOnBurn: true,
        }),
      ),
    ).toEqual([
      "Revoke its badge. Its next call is refused.",
      "Rotate api_key and aws_key, the two keys it owns. The old values stop working at once.",
      "Flag agent-analyst-1/api_key and crm_db_url, which it saw in plain text, for their owners.",
      "Leave its child reader-01 running, and every other agent.",
      "Mark it for a replacement. A fresh worker resumes the work it finished; anything it touched after the first evidence waits for a person.",
    ]);
  });

  it("says plainly when there is nothing to rotate or flag", () => {
    expect(planText(agentPlan({ agent, entries: [] })).slice(1, 4)).toEqual([
      "It owns no keys, so nothing is rotated.",
      "It saw no shared key in plain text, so nothing is flagged.",
      "Leave every other agent running.",
    ]);
  });

  it("speaks generally until the ledger loads", () => {
    expect(planText(agentPlan({ agent })).slice(1, 3)).toEqual([
      "Rotate the keys it owns. The old values stop working at once.",
      "Flag the shared keys it saw in plain text, for their owners.",
    ]);
  });

  it("promises no replacement without a session to burn", () => {
    const lines = agentPlan({
      agent: { ...agent, sessionId: null },
      entries: [],
      replaceOnBurn: true,
    });
    expect(lines.map((line) => line.id)).not.toContain("replace");
  });

  it("sets key names apart so the dialog can show them in mono", () => {
    const rotate = agentPlan({ agent, credentials: ["api_key"] })[1];
    expect(rotate.parts).toEqual([
      "Rotate ",
      { key: "api_key" },
      ", the one key it owns. The old value stops working at once.",
    ]);
  });
});

describe("rolePlan", () => {
  it("lists what a role burn does, from its running agents and credentials", () => {
    expect(
      planText(
        rolePlan({
          role: "worker",
          running: ["worker-02", "worker-03"],
          credentials: ["aws_key", "api_key"],
          childrenElsewhere: ["reader-01"],
          replaceOnBurn: true,
        }),
      ),
    ).toEqual([
      "Revoke the badges of all 2 running workers. Their next calls are refused.",
      "Rotate each one's own api_key and aws_key. The old values stop working at once.",
      "Flag the shared keys any of them saw in plain text, for their owners.",
      "Leave reader-01 running: it is another role, though one of them started it.",
      "Leave every other role running.",
      "Start no replacements: a role burn never replaces its agents.",
    ]);
  });

  it("says there is nothing to burn when nothing runs", () => {
    expect(planText(rolePlan({ role: "worker", running: [] }))).toEqual([
      "No workers are running, so there is nothing to burn.",
    ]);
  });
});

describe("containmentSteps", () => {
  const data = makeConsole();
  const agent = data.model.byKey.get("sess-w3")!;
  const outcome = (over: Partial<BurnOutcome> = {}): BurnOutcome => ({
    target: { scope: "agent", agent },
    burned: [result()],
    failed: [],
    at: "2026-11-08T19:45:00Z",
    ...over,
  });

  it("ticks revoked, each owned key, each shared key, then everyone else", () => {
    const steps = containmentSteps(outcome(), {
      untouched: 19,
      childrenLeft: ["reader-01"],
    });
    expect(steps.map((step) => [step.title, step.sub])).toEqual([
      ["Badge revoked", "Its next call is refused"],
      ["api_key rotated", "The old value no longer works"],
      ["aws_key replaced", "The old key is inactive"],
      [
        "crm_db_url sent to its owner",
        "Shared; its owner decides whether to rotate",
      ],
      ["19 agents untouched", "Including its child, reader-01"],
    ]);
    expect(steps[0].hot).toBe(true);
    expect(steps.some((step) => step.error)).toBe(false);
  });

  it("lists keys by name whatever order the answer gave", () => {
    const steps = containmentSteps(
      outcome({
        burned: [
          result({ rotated: [`burn/${ME}/aws_key`, `burn/${ME}/api_key`] }),
        ],
      }),
    );
    expect(steps.slice(1, 3).map((step) => step.title)).toEqual([
      "api_key rotated",
      "aws_key replaced",
    ]);
  });

  it("says the next call was refused once one has been", () => {
    expect(containmentSteps(outcome(), { refused: true })[0].sub).toBe(
      "Its next call was refused",
    );
  });

  it("doesn't count the untouched when it isn't known", () => {
    expect(containmentSteps(outcome()).at(-1)?.title).toBe(
      "Every other agent untouched",
    );
  });

  it("says when the revocation wasn't confirmed", () => {
    const steps = containmentSteps(
      outcome({ burned: [result({ revoked: false })] }),
    );
    expect(steps[0].error).toMatch(/couldn't confirm the revocation/);
    expect(steps[0].error).toMatch(/Burn worker-03 again/);
  });

  it("says which part didn't finish when the API reports a failure", () => {
    const steps = containmentSteps(
      outcome({
        burned: [],
        failed: [{ agent_id: ME, error: "timed out" }],
      }),
    );
    expect(steps.map((step) => step.id)).toEqual([
      "revoked",
      "unfinished",
      "untouched",
    ]);
    expect(steps[0].error).toBeUndefined();
    expect(steps[1].error).toBe(
      "Didn't finish: timed out. Its badge is revoked; burn worker-03 again to rotate its keys and flag what it saw.",
    );
  });

  it("says so when there was nothing to rotate or flag", () => {
    const steps = containmentSteps(
      outcome({ burned: [result({ rotated: [], flagged: [] })] }),
    );
    expect(steps[1].title).toBe("Nothing to rotate or flag");
  });

  it("sums a role burn and names the agents that didn't finish", () => {
    const steps = containmentSteps(
      {
        target: { scope: "role", role: "worker" },
        burned: [
          result({ agent_id: "agent-worker-2" }),
          result({ agent_id: ME }),
        ],
        failed: [{ agent_id: "agent-worker-5", error: "timed out" }],
      },
      { untouched: 3, nameOf: (id) => data.model.byAgentId.get(id)?.[0].name },
    );
    expect(steps.map((step) => step.title)).toEqual([
      "3 workers revoked",
      "4 keys rotated",
      "crm_db_url sent to its owner",
      "1 worker not finished",
      "3 agents untouched",
    ]);
    expect(steps[1].sub).toBe("Each worker's own api_key and aws_key");
    expect(steps[3].error).toMatch(/^worker-05 didn't finish/);
  });
});
