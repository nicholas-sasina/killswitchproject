import { describe, expect, it, vi } from "vitest";
import type { LedgerEvent } from "../contracts";
import { callersSince, createPulseBus } from "./activity";

const event = (
  id: string,
  source: LedgerEvent["source"],
  session: string | null,
): LedgerEvent => ({
  event_id: id,
  ts: "2026-11-08T19:42:09Z",
  agent_id: `agent-${session ?? "scenario"}`,
  session_id: session,
  source,
  action: "recorded",
});

describe("agent activity", () => {
  const events = [
    event("evt_1", "tool_response", "a"),
    event("evt_2", "gateway", "b"),
    event("evt_3", "burn", "c"),
    event("evt_4", "egress", null),
  ];

  it("finds agents that made a call after the last poll", () => {
    expect([...callersSince(events, "evt_1")]).toEqual(["b", "agent-scenario"]);
  });

  it("dims nothing on the first poll", () => {
    expect(callersSince(events, undefined).size).toBe(0);
  });

  it("tells only the squares that listen for that agent", () => {
    const bus = createPulseBus();
    const a = vi.fn();
    const b = vi.fn();
    const stop = bus.subscribe("a", a);
    bus.subscribe("b", b);
    bus.emit(["a"]);
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).not.toHaveBeenCalled();
    stop();
    bus.emit(["a"]);
    expect(a).toHaveBeenCalledTimes(1);
  });
});
