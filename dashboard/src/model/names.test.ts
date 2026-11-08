import { describe, expect, it } from "vitest";
import { displayName, displayNames, numberAgents, roleOf } from "./names";

const agent = (key: string, role: string, createdAt?: string) => ({
  key,
  agentId: `agent-${role}-${key}`,
  role,
  createdAt,
});

describe("display names", () => {
  it("numbers agents by start time within their role, oldest first", () => {
    const names = displayNames([
      agent("s3", "worker", "2026-11-08T19:12:00Z"),
      agent("s1", "worker", "2026-11-08T19:10:00Z"),
      agent("s9", "reader", "2026-11-08T19:30:00Z"),
      agent("s2", "worker", "2026-11-08T19:11:00Z"),
    ]);
    expect(Object.fromEntries(names)).toEqual({
      s1: "worker-01",
      s2: "worker-02",
      s3: "worker-03",
      s9: "reader-01",
    });
  });

  it("gives a replacement the next number, not its predecessor's", () => {
    const burned = agent("old", "worker", "2026-11-08T19:12:00Z");
    const others = [1, 2, 3].map((n) =>
      agent(`w${n}`, "worker", `2026-11-08T19:0${n}:00Z`),
    );
    const replacement = agent("new", "worker", "2026-11-08T19:43:00Z");
    const before = displayNames([...others, burned]);
    const after = displayNames([replacement, ...others, burned]);
    expect(before.get("old")).toBe("worker-04");
    expect(after.get("old")).toBe("worker-04");
    expect(after.get("new")).toBe("worker-05");
  });

  it("is the same whatever order the API lists agents in", () => {
    const list = [
      agent("b", "worker", "2026-11-08T19:00:00Z"),
      agent("a", "worker", "2026-11-08T19:00:00Z"),
      agent("c", "worker"),
    ];
    const forward = numberAgents(list);
    const backward = numberAgents([...list].reverse());
    expect(forward).toEqual(backward);
    // Ties go by key; no start time sorts last.
    expect(Object.fromEntries(forward)).toEqual({ a: 1, b: 2, c: 3 });
  });

  it("pads to two digits and grows past 99", () => {
    expect(displayName("worker", 7)).toBe("worker-07");
    expect(displayName("worker", 123)).toBe("worker-123");
  });

  it("takes a scenario agent's role from its agent id when /events has none", () => {
    expect(roleOf({ agentId: "agent-reader", role: null })).toBe("reader");
    expect(roleOf({ agentId: "agent-reader", role: "triage" })).toBe("triage");
  });
});
