import { describe, expect, it } from "vitest";
import type { LedgerEvent } from "../contracts";
import {
  burnReason,
  eventLine,
  eventSentence,
  withoutSubject,
} from "./sentences";

const names: Record<string, string> = {
  "agent-worker-7f": "worker-07",
  "agent-reader-11": "reader-11",
};
const context = {
  nameOf: ({ agentId }: { agentId?: string | null }) =>
    agentId ? names[agentId] : undefined,
};

function event(extra: Partial<LedgerEvent>): LedgerEvent {
  return {
    event_id: "evt_1",
    ts: "2026-11-08T19:42:09Z",
    agent_id: "agent-worker-7f",
    source: "gateway",
    action: "recorded",
    ...extra,
  };
}

const line = (extra: Partial<LedgerEvent>) =>
  eventLine(eventSentence(event(extra), context));

describe("events as plain sentences", () => {
  it("names the agent and the key, never the raw ids", () => {
    expect(
      line({
        action: "recorded",
        source: "tool_response",
        tool: "read_config",
        secret_ref: "burn/shared/crm_db_url",
      }),
    ).toBe("worker-07 saw crm_db_url through read_config");
  });

  it("says what a block proves", () => {
    expect(
      line({
        action: "blocked",
        source: "egress",
        secret_ref: "burn/shared/crm_db_url",
        destination: "paste.example",
      }),
    ).toBe("worker-07 tried to send crm_db_url to paste.example; blocked");
    expect(
      line({ action: "blocked", source: "egress", destination: "x.test" }),
    ).toBe("worker-07 was stopped from reaching x.test");
    const handoff = eventSentence(
      event({
        action: "blocked",
        source: "handoff",
        secret_ref: "burn/a/api_key",
        target_agent_id: "agent-reader-11",
      }),
      context,
    );
    expect(eventLine(handoff)).toBe(
      "worker-07 tried to pass api_key to reader-11; Burn removed it",
    );
    expect(handoff.evidence).toBe(true);
  });

  it("tells a person's burn from a canary's", () => {
    expect(
      line({
        action: "revoked",
        source: "burn",
        actor: "priya@example.test",
        detail: "1 session(s) revoked (read an unverified vendor page)",
      }),
    ).toBe(
      "worker-07 burned by priya@example.test: read an unverified vendor page",
    );
    expect(
      line({
        action: "revoked",
        source: "burn",
        actor: "system",
        secret_ref: "burn/agent-worker-7f/canary_1",
      }),
    ).toBe("worker-07 burned: its canary was used outside Burn");
    expect(
      line({
        action: "canary_used",
        source: "provider",
        detail: "canary presented (from 203.0.113.9)",
      }),
    ).toBe("worker-07's canary was used from 203.0.113.9, outside Burn");
  });

  it("puts a key first when the key is the news", () => {
    const flagged = eventSentence(
      event({
        action: "flagged",
        source: "burn",
        secret_ref: "burn/shared/crm_db_url",
        flag_level: "reported",
      }),
      context,
    );
    expect(flagged.who).toBe("crm_db_url");
    expect(flagged.whoKind).toBe("key");
    expect(eventLine(flagged)).toBe(
      "crm_db_url flagged for its owner: worker-07 was burned holding it",
    );
    expect(
      line({
        action: "dismissed",
        agent_id: "system",
        secret_ref: "burn/shared/crm_db_url",
        actor: "dana@example.test",
      }),
    ).toBe("crm_db_url flag dismissed by its owner, dana@example.test");
  });

  it("calls an unknown badge an unknown caller", () => {
    const refused = eventSentence(
      event({
        action: "refused",
        agent_id: "system",
        detail: "unknown badge client 4f2a",
      }),
      context,
    );
    expect(refused.whoKind).toBe("caller");
    expect(eventLine(refused)).toBe(
      "An unknown caller was refused before any tool ran",
    );
  });

  it("drops the subject for an agent's own timeline", () => {
    expect(
      withoutSubject(
        eventSentence(
          event({ action: "canary_used", source: "provider" }),
          context,
        ),
      ),
    ).toBe("Its canary was used, outside Burn");
    expect(
      withoutSubject(
        eventSentence(
          event({ action: "rejected", secret_ref: "burn/a/api_key" }),
          context,
        ),
      ),
    ).toBe("Presented its old api_key; the provider refused it");
  });

  it("still reads an action this build has not heard of", () => {
    expect(line({ action: "quarantined_item" as LedgerEvent["action"] })).toBe(
      "worker-07 quarantined item",
    );
  });

  it("falls back to the agent id when the fleet has no name for it", () => {
    expect(line({ agent_id: "agent-x", action: "exited" })).toBe(
      "agent-x finished",
    );
  });

  it("reads the reason out of a burn's detail", () => {
    expect(burnReason("2 session(s) revoked (read read_ticket:T-1001)")).toBe(
      "read read_ticket:T-1001",
    );
    expect(burnReason("Mock badge revocation.")).toBe("");
  });
});
