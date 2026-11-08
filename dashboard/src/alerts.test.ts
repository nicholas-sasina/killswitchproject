import { describe, expect, it } from "vitest";
import {
  attentionCount,
  isBlockedAtExit,
  isUnknownCaller,
  toAlerts,
} from "./alerts";
import type { LedgerEvent } from "./contracts";

let next = 0;
function event(fields: Partial<LedgerEvent>): LedgerEvent {
  next += 1;
  return {
    event_id: `evt_${String(next).padStart(4, "0")}`,
    ts: "2026-11-08T05:00:00.000Z",
    agent_id: "agent-reader",
    source: "egress",
    action: "blocked",
    ...fields,
  } as LedgerEvent;
}

function only(fields: Partial<LedgerEvent>) {
  const alerts = toAlerts([event(fields)]);
  expect(alerts).toHaveLength(1);
  return alerts[0];
}

describe("blocked events", () => {
  it("call a block that names a credential a credential leaving the network", () => {
    const alert = only({
      detail: "credential_detected",
      secret_ref: "burn/agent-reader/api_key",
      fingerprint: "f".repeat(64),
    });
    expect(alert.tier).toBe("high");
    expect(alert.title).toBe("Credential blocked leaving the network");
  });

  it.each([
    "destination_not_allowed",
    "destination_port_not_allowed",
    "role_destination_not_allowed",
    "private_destination",
    "payload_too_large",
  ])("never call a %s block a credential", (detail) => {
    const alert = only({ detail });
    expect(alert.tier).toBe("warn");
    expect(alert.title).toBe("Outbound request blocked by policy");
    expect(alert.why).toContain(detail.replaceAll("_", " "));
    expect(alert.why).toContain("No credential was found");
  });

  it("keep a credential stopped in a handoff inside the system", () => {
    const alert = only({
      source: "handoff",
      tool: "send_message",
      detail: "send_message blocked: known credential",
      secret_ref: "burn/agent-reader/api_key",
    });
    expect(alert.tier).toBe("warn");
    expect(alert.title).toBe("Credential blocked in a handoff");
  });
});

describe("refused events", () => {
  it.each([
    ["gateway", "badge revoked"],
    ["egress", "session_inactive"],
  ] as const)(
    "a %s refusal after a burn is containment working",
    (source, detail) => {
      const alert = only({ source, action: "refused", detail });
      expect(alert.title).toBe("Burned agent's call refused");
      expect(alert.tier).toBe("warn");
    },
  );

  it.each([
    ["gateway", "unknown badge client abc"],
    ["egress", "session_required"],
  ] as const)("a %s refusal of an unknown caller is high", (source, detail) => {
    const alert = only({
      source,
      action: "refused",
      detail,
      agent_id: "system",
    });
    expect(alert.title).toBe("Unknown caller refused");
    expect(alert.tier).toBe("high");
  });

  it("a tool or recipient outside the role is a policy refusal, not a burn", () => {
    const alert = only({
      source: "gateway",
      action: "refused",
      detail: "tool not allowed for this agent: read_config",
    });
    expect(alert.title).toBe("Call outside the agent's role refused");
    expect(alert.why).toContain("tool not allowed for this agent: read config");
  });

  it("a withheld tool response says so", () => {
    const alert = only({
      source: "tool_response",
      action: "refused",
      detail: "read_config output too large to check; withheld from the agent",
    });
    expect(alert.title).toBe("Tool response withheld");
  });
});

describe("what counts", () => {
  it("bookkeeping and unknown actions are not alerts", () => {
    const alerts = toAlerts([
      event({ source: "session", action: "issued" }),
      event({ source: "tool_response", action: "recorded" }),
      event({ source: "burn", action: "rotated" }),
      event({ source: "instance", action: "started" }),
      event({ action: "renamed" as LedgerEvent["action"] }),
    ]);
    expect(alerts).toEqual([]);
  });

  it("the attention count leaves out noted alerts", () => {
    const alerts = toAlerts([
      event({ source: "provider", action: "canary_used" }),
      event({ detail: "destination_not_allowed" }),
      event({ source: "burn", action: "revoked" }),
    ]);
    expect(alerts.map((alert) => alert.tier)).toEqual(["crit", "warn", "info"]);
    expect(attentionCount(alerts)).toBe(2);
  });
});

describe("the rules other views share", () => {
  it("counts a block at the network's exit only when it carries a credential", () => {
    expect(
      isBlockedAtExit(
        event({ action: "blocked", secret_ref: "burn/a/api_key" }),
      ),
    ).toBe(true);
    expect(
      isBlockedAtExit(
        event({ action: "blocked", detail: "credential_detected" }),
      ),
    ).toBe(true);
    expect(isBlockedAtExit(event({ action: "blocked", detail: "port" }))).toBe(
      false,
    );
    expect(
      isBlockedAtExit(
        event({ action: "blocked", source: "gateway", secret_ref: "burn/a/k" }),
      ),
    ).toBe(false);
  });

  it("names a caller Burn never enrolled, and nothing else", () => {
    expect(isUnknownCaller(event({ action: "unregistered" }))).toBe(true);
    expect(
      isUnknownCaller(
        event({ action: "refused", detail: "unknown badge client c-91" }),
      ),
    ).toBe(true);
    expect(
      isUnknownCaller(event({ action: "refused", detail: "session_required" })),
    ).toBe(true);
    expect(
      isUnknownCaller(event({ action: "refused", detail: "badge revoked" })),
    ).toBe(false);
  });
});
