import { describe, expect, it } from "vitest";
import { ApiError } from "../../api";
import { mergeFleet } from "../../fleet";
import { buildConsole } from "../../model";
import {
  decided,
  evidenceSentence,
  firstEvidence,
  flagTag,
  levelRank,
  ownerRotations,
  reasonProblem,
  reopenNote,
  resolveError,
  safeBy,
  seenByKey,
} from "./describe";
import { event, flag, instance, today } from "./testConsole";

describe("flag words", () => {
  it("orders levels seen < attempted < reported", () => {
    expect(levelRank("seen")).toBe(1);
    expect(levelRank("attempted")).toBe(2);
    expect(levelRank("reported")).toBe(3);
    expect(levelRank(undefined)).toBe(0);
  });

  it("tags a shared key and another agent's key", () => {
    expect(flagTag(flag({ flag_level: "reported" }))).toBe(
      "Shared key / reported",
    );
    expect(
      flagTag(flag({ owner_agent_id: "agent-analyst-9c", flag_level: "seen" })),
    ).toBe("Agent key / seen");
    expect(flagTag(flag({ owner_agent_id: undefined }))).toBe(
      "Shared key / seen",
    );
  });

  it("checks a reason the way the API does", () => {
    expect(reasonProblem("ok")).toContain("at least 3 characters");
    expect(reasonProblem("   ok   ")).toContain("at least 3 characters");
    expect(reasonProblem("fine")).toBeUndefined();
    expect(reasonProblem("x".repeat(301))).toContain("300 characters");
  });

  it("sends `by` only when the API's pattern accepts it", () => {
    expect(safeBy("priya.nair@example.com")).toBe("priya.nair@example.com");
    expect(safeBy("o'brien+burn@example.com")).toBeUndefined();
    expect(safeBy(`${"a".repeat(60)}@example.com`)).toBeUndefined();
    expect(safeBy(undefined)).toBeUndefined();
  });

  it("puts a refused decision in plain words", () => {
    expect(resolveError(new ApiError("x", 400), "crm_db_url")).toContain(
      "never include a key's value",
    );
    expect(resolveError(new ApiError("x", 404), "crm_db_url")).toBe(
      "crm_db_url is no longer flagged. Someone may have resolved it already.",
    );
    expect(resolveError(new ApiError("x", 409), "crm_db_url")).toContain(
      "changed while you were deciding",
    );
    expect(
      resolveError(new ApiError("API unreachable.", 0), "crm_db_url"),
    ).toBe("API unreachable.");
  });

  it("says what would reopen a dismissed flag", () => {
    expect(reopenNote("seen")).toBe(
      "It reopens only if evidence at attempted or reported arrives.",
    );
    expect(reopenNote("attempted")).toBe(
      "It reopens only if evidence at reported arrives.",
    );
    expect(reopenNote("reported")).toContain("Nothing reopens it");
  });

  it("notes a decision on the flag until the poll confirms it", () => {
    const after = decided(flag(), "dismiss", "test key", "p@x.io", "T");
    expect(after.state).toBe("dismissed");
    expect(after.resolution).toEqual({
      action: "dismiss",
      reason: "test key",
      by: "p@x.io",
      at: "T",
      level: "seen",
    });
    expect(decided(flag(), "rotate", "r", undefined, "T").state).toBe(
      "rotated",
    );
  });
});

describe("evidence", () => {
  it("takes the earliest evidence, by time when every level has one", () => {
    const first = firstEvidence(
      flag({
        flag_level: "attempted",
        evidence: {
          seen: { source: "burn", ts: today(9, 5) },
          attempted: { source: "egress", ts: today(9, 1) },
        },
      }),
    );
    expect(first?.level).toBe("attempted");
    expect(first?.at).toBe(today(9, 1));
  });

  it("falls back to the lowest level, timed by when the key was flagged", () => {
    const first = firstEvidence(
      flag({
        flagged_at: today(7, 42),
        evidence: { seen: { source: "burn", agent_id: "agent-worker-a" } },
      }),
    );
    expect(first).toEqual({
      level: "seen",
      evidence: { source: "burn", agent_id: "agent-worker-a" },
      at: today(7, 42),
    });
    expect(firstEvidence(flag({ evidence: undefined }))).toBeUndefined();
  });

  it("says what each source of evidence shows", () => {
    expect(evidenceSentence({ source: "burn" }, "worker-07")).toBe(
      "worker-07 was burned holding it",
    );
    expect(
      evidenceSentence(
        { source: "tool_response", tool: "read_config" },
        "worker-07",
      ),
    ).toBe("worker-07 saw it through read_config");
    expect(evidenceSentence({ source: "handoff" }, "reader-04")).toBe(
      "reader-04 was handed it in a message",
    );
    expect(evidenceSentence({ source: "egress" }, undefined)).toBe(
      "An agent tried to send it out; the egress proxy stopped it",
    );
    expect(evidenceSentence({ source: "provider" }, undefined)).toBe(
      "Presented at a monitored service",
    );
  });
});

describe("who saw a key", () => {
  const instances = [
    instance("s1", "revoked", { created_at: today(1, 0) }),
    instance("s2", "active", { created_at: today(2, 0) }),
    instance("s3", "active", { created_at: today(3, 0) }),
  ];
  const model = buildConsole({
    fleet: mergeFleet([], instances),
    events: [],
    now: new Date(),
  });
  const ref = "burn/shared/crm_db_url";

  it("lists each agent once, in the order they first saw it", () => {
    const events = [
      event({
        action: "recorded",
        agent_id: "agent-worker-s3",
        secret_ref: ref,
      }),
      event({
        action: "recorded",
        agent_id: "agent-worker-s1",
        secret_ref: ref,
      }),
      event({
        action: "recorded",
        agent_id: "agent-worker-s3",
        secret_ref: ref,
      }),
      event({
        action: "recorded",
        agent_id: "agent-worker-s2",
        secret_ref: "burn/shared/other",
      }),
      event({ action: "rotated", agent_id: "system", secret_ref: ref }),
    ].reverse();
    const seen = seenByKey(events, model, new Set([ref]));
    expect(seen.get(ref)?.map((agent) => agent.name)).toEqual([
      "worker-03",
      "worker-01",
    ]);
    expect(seen.has("burn/shared/other")).toBe(false);
  });
});

describe("owner rotations", () => {
  it("reads one rotation per key, newest first, with the owner's reason", () => {
    const events = [
      event({
        action: "rotated",
        agent_id: "system",
        secret_ref: "burn/shared/crm_db_url",
        ts: today(8, 0),
        detail:
          "priya@example.com rotated this flagged credential (was seen, 1 instance(s)): rotated after review",
        actor: "priya@example.com",
      }),
      event({
        action: "rotated",
        agent_id: "system",
        secret_ref: "burn/shared/crm_db_url",
        ts: today(9, 0),
        detail: "Mock owner rotation of a flagged credential.",
      }),
      event({
        action: "rotated",
        agent_id: "agent-worker-s1",
        secret_ref: "burn/agent-worker-s1/api_key",
      }),
    ].reverse();
    expect(ownerRotations(events)).toEqual([
      {
        ref: "burn/shared/crm_db_url",
        at: today(9, 0),
        actor: undefined,
        reason: undefined,
      },
    ]);
    expect(ownerRotations(events.slice(2))[0].reason).toBe(
      "rotated after review",
    );
  });
});
