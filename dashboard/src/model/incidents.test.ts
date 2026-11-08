import { describe, expect, it } from "vitest";
import type { Flag, Instance, LedgerEvent } from "../contracts";
import { mergeFleet } from "../fleet";
import { buildConsole } from "./console";
import { incidentCounts, incidents } from "./incidents";

const now = new Date(2026, 10, 8, 20, 0);
const ts = (m: number) => new Date(2026, 10, 8, 19, m).toISOString();

function burned(session: string, extra: Partial<Instance> = {}): Instance {
  return {
    session_id: session,
    agent_id: `agent-worker-${session}`,
    role: "worker",
    status: "revoked",
    created_at: ts(0),
    ...extra,
  };
}

function burnEvent(
  session: string,
  m: number,
  action: LedgerEvent["action"],
  ref?: string,
  detail?: string,
): LedgerEvent {
  return {
    event_id: `evt_${String(m).padStart(2, "0")}_${action}_${session}_${ref}`,
    ts: ts(m),
    agent_id: `agent-worker-${session}`,
    session_id: session,
    source: "burn",
    action,
    secret_ref: ref,
    detail,
  };
}

const flag = (ref: string, state: Flag["state"]): Flag => ({
  secret_ref: ref,
  flag_level: "reported",
  instance_count: 1,
  state,
});

describe("incidents", () => {
  const events = [
    burnEvent("a", 42, "revoked"),
    burnEvent("a", 42, "rotated", "burn/agent-worker-a/api_key"),
    burnEvent("a", 42, "flagged", "burn/shared/crm_db_url"),
    burnEvent("b", 30, "revoked"),
    burnEvent(
      "b",
      30,
      "flagged",
      "burn/agent-worker-b/aws_key",
      "rotation failed: throttled",
    ),
    burnEvent("c", 10, "revoked"),
  ];
  const model = buildConsole({
    fleet: mergeFleet(
      [],
      [
        burned("a"),
        burned("b"),
        burned("c"),
        burned("d", {
          replacement: {
            status: "needs_person",
            cutoff: ts(5),
            cutoff_source: "burn_time",
            done: [],
            quarantined: [],
          },
        }),
      ],
    ),
    events,
    now,
  });

  it("keeps an incident open while a key it flagged waits on its owner", () => {
    const list = incidents(model, events, [
      flag("burn/shared/crm_db_url", "open"),
    ]);
    expect(
      list.map((incident) => [incident.agent.key, incident.status]),
    ).toEqual([
      ["a", "keys-open"],
      ["b", "rotation-failed"],
      ["c", "burned"],
      ["d", "needs-person"],
    ]);
    expect(list[0].openKeys).toEqual(["burn/shared/crm_db_url"]);
    expect(list[0].rotated).toEqual(["burn/agent-worker-a/api_key"]);
    expect(
      incidentCounts(list, [flag("burn/shared/crm_db_url", "open")]),
    ).toEqual({ all: 4, open: 3, closed: 1, keysWaiting: 1 });
  });

  it("closes it once the owner decides", () => {
    const list = incidents(model, events, [
      flag("burn/shared/crm_db_url", "dismissed"),
    ]);
    expect(list[0].status).toBe("burned");
    expect(incidentCounts(list, []).keysWaiting).toBe(0);
  });

  it("clears a failed rotation that was finished later", () => {
    const later = [
      ...events,
      burnEvent("b", 50, "rotated", "burn/agent-worker-b/aws_key"),
    ];
    const list = incidents(model, later, []);
    expect(list.find((incident) => incident.agent.key === "b")?.status).toBe(
      "burned",
    );
  });

  it("keeps a canary burn open when its rotation failed without naming a key", () => {
    // The canary path reports a failed or deferred rotation for the whole
    // agent: a flagged event with no key.
    const canary = [
      ...events,
      burnEvent(
        "c",
        11,
        "flagged",
        undefined,
        "rotation failed: session stays revoked, credentials NOT rotated",
      ),
    ];
    const status = (list: LedgerEvent[]) =>
      incidents(model, list, []).find((incident) => incident.agent.key === "c")
        ?.status;
    expect(status(canary)).toBe("rotation-failed");
    // Burning it again by hand rotates its keys and closes it.
    expect(
      status([
        ...canary,
        burnEvent("c", 20, "rotated", "burn/agent-worker-c/api_key"),
      ]),
    ).toBe("burned");
  });
});
