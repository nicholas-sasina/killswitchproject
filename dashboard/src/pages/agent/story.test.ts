import { describe, expect, it } from "vitest";
import type { Instance, LedgerEntry, LedgerEvent } from "../../contracts";
import {
  event,
  makeConsole,
  NOW,
  ROLES,
  sampleEvents,
  sampleInstances,
} from "../../features/burn/fixture";
import { burnTrigger } from "../../model/burns";
import { findAgent, type ConsoleModel } from "../../model/console";
import { clockTime, dayTime } from "../../model/time";
import {
  agentEvents,
  beforeBurnSteps,
  blastSentence,
  CLOUDTRAIL_LAG,
  evidenceSentence,
  heroLede,
  replacementOf,
  takeoverOf,
  timelineItems,
} from "./story";

const at = (minutesBefore: number) =>
  new Date(NOW.valueOf() - minutesBefore * 60_000).toISOString();

function agentIn(model: ConsoleModel, id: string) {
  const agent = findAgent(model, id);
  if (!agent) throw new Error(`no agent ${id}`);
  return agent;
}

function setup(instances: Instance[] = sampleInstances()) {
  const data = makeConsole({ instances });
  return { data, model: data.model };
}

const crm: LedgerEntry = {
  secret_ref: "burn/shared/crm_db_url",
  fingerprint: "fp-crm",
  secret_type: "other",
  owner: "shared",
  source: "tool_response",
  first_seen: at(14),
  status: "active",
  exposure: "seen",
};

describe("agentEvents", () => {
  it("keeps only this agent's events", () => {
    const { model } = setup();
    const mine = agentEvents(
      [
        ...sampleEvents(),
        event({ action: "recorded", agent_id: "agent-worker-2" }),
      ],
      agentIn(model, "sess-w4"),
      model,
    );
    expect(mine.length).toBe(sampleEvents().length);
    expect(mine.every((item) => item.agent_id === "agent-worker-4")).toBe(true);
  });
});

describe("evidenceSentence", () => {
  it("says what it saw and that it tried to send it out", () => {
    const { model } = setup();
    const agent = agentIn(model, "sess-w4");
    expect(
      evidenceSentence([crm], agent, agentEvents(sampleEvents(), agent, model)),
    ).toBe("It saw one shared key in plain text and tried to send it out.");
  });

  it("names its own key when that is the one it tried to send", () => {
    const { model } = setup();
    const agent = agentIn(model, "sess-w2");
    const own: LedgerEntry = {
      ...crm,
      secret_ref: "burn/agent-worker-2/api_key",
      owner: "agent",
      owner_agent_id: "agent-worker-2",
      secret_type: "api_key",
    };
    const blocked = event({
      action: "blocked",
      agent_id: "agent-worker-2",
      source: "egress",
      secret_ref: own.secret_ref!,
    });
    expect(evidenceSentence([crm, own], agent, [blocked])).toBe(
      "It saw one shared key in plain text, and tried to send api_key out.",
    );
    expect(evidenceSentence([own], agent, [blocked])).toBe(
      "It tried to send api_key out; Burn stopped it.",
    );
  });

  it("says nothing before its ledger loads, or when there is nothing to say", () => {
    const { model } = setup();
    const agent = agentIn(model, "sess-w2");
    expect(evidenceSentence(undefined, agent, [])).toBeUndefined();
    expect(evidenceSentence([], agent, [])).toBeUndefined();
  });
});

describe("heroLede", () => {
  it("describes a running agent", () => {
    const { model } = setup();
    const agent = agentIn(model, "sess-w2");
    expect(
      heroLede({
        agent,
        model,
        entries: [crm],
        events: [],
        cause: { kind: "unknown" },
      }),
    ).toBe(
      `A worker, one of 3. Running for 58 minutes. Started by priya@example.com at ${dayTime(agent.createdAt, model.now)}. It saw one shared key in plain text.`,
    );
  });

  it("describes a burned agent with who burned it and why", () => {
    const { model } = setup();
    const agent = agentIn(model, "sess-w4");
    const events = agentEvents(sampleEvents(), agent, model);
    const lede = heroLede({
      agent,
      model,
      events,
      cause: burnTrigger(agent, events),
    });
    expect(lede).toMatch(
      /^A worker\. Burned by priya@example\.com at .+: read an unverified vendor page\. Started by priya@example\.com at .+\.$/,
    );
  });

  it("uses the answer's time for a burn made from the page", () => {
    const { model } = setup();
    const agent = agentIn(model, "sess-w2");
    const burnedAt = at(0);
    const lede = heroLede({
      agent: { ...agent, state: "burned", burnedAtFrom: "last-seen" },
      model,
      events: [],
      cause: { kind: "person", who: "you", reason: "" },
      burnedAt,
    });
    expect(lede).toContain(`Burned by you at ${clockTime(burnedAt)}.`);
    expect(lede).not.toContain("Its last call");
  });

  it("describes a finished agent", () => {
    const { model } = setup();
    const lede = heroLede({
      agent: agentIn(model, "sess-w1"),
      model,
      events: [],
      cause: { kind: "unknown" },
    });
    expect(lede).toMatch(/^A worker\. Finished at .+ after 19 minutes\./);
  });
});

describe("timelineItems", () => {
  const { data, model } = setup();
  const agent = agentIn(model, "sess-w4");

  it("marks reading a key only when its ledger calls that key evidence", () => {
    const read = event({
      action: "recorded",
      agent_id: "agent-worker-4",
      source: "tool_response",
      tool: "read_config",
      secret_ref: "burn/shared/crm_db_url",
    });
    expect(timelineItems([read], data.sentenceContext)[0].tone).toBe("routine");
  });

  it("tells its story newest first, without the burn's key-by-key events", () => {
    const items = timelineItems(
      agentEvents(sampleEvents(), agent, model),
      data.sentenceContext,
      agent.burnedAt,
      new Set(["burn/shared/crm_db_url"]),
    );
    expect(items.map((item) => item.tone)).toEqual([
      "burn",
      "evidence",
      "evidence",
      "routine",
    ]);
    expect(items.some((item) => /rotated|flagged/.test(item.text))).toBe(false);
  });

  it("folds repeats and the refusals after the burn into one line each", () => {
    const seen = (minutes: number): LedgerEvent =>
      event({
        action: "recorded",
        agent_id: "agent-worker-4",
        source: "tool_response",
        tool: "read_config",
        secret_ref: "burn/shared/crm_db_url",
        ts: at(minutes),
      });
    const refused = (minutes: number): LedgerEvent =>
      event({
        action: "refused",
        agent_id: "agent-worker-4",
        detail: "revoked session",
        ts: at(minutes),
      });
    const items = timelineItems(
      [refused(2), refused(3), refused(4), seen(13), seen(14)],
      data.sentenceContext,
      at(10),
      new Set(["burn/shared/crm_db_url"]),
    );
    expect(items.map((item) => [item.text, item.tone])).toEqual([
      ["Its calls were refused 3 times after the burn", "after"],
      [expect.stringMatching(/crm_db_url.*\(2 times\)$/), "evidence"],
    ]);
  });
});

describe("beforeBurnSteps", () => {
  it("lists what a burn would do, as on the running board", () => {
    const steps = beforeBurnSteps({
      agentId: "agent-worker-2",
      entries: [crm],
      credentials: ["api_key", "aws_key"],
      othersRunning: 19,
      childrenRunning: ["reader-11"],
    });
    expect(steps.map((step) => [step.title, step.sub])).toEqual([
      ["Revoke its badge", "Its next call is refused"],
      ["Rotate api_key", "Its own copy; nobody else uses it"],
      ["Replace aws_key", "The old key goes inactive"],
      ["Flag crm_db_url", "Shared; its owner decides"],
      ["Leave everyone else running", "19 agents, including reader-11"],
    ]);
  });

  it("says when nobody else is running", () => {
    const steps = beforeBurnSteps({
      agentId: "agent-worker-2",
      entries: [],
      credentials: [],
      othersRunning: 0,
      childrenRunning: [],
    });
    expect(steps.at(-1)?.sub).toBe("No other agents are running");
  });
});

describe("replacementOf", () => {
  it("names who took over, the cut-off and what was quarantined", () => {
    const { model } = setup();
    const handover = replacementOf(agentIn(model, "sess-w4"), model, ROLES[0]);
    expect(handover).toMatchObject({
      headline: "worker-05 took over",
      other: { name: "worker-05", key: "sess-w5" },
      quarantined: ["w4-07", "w4-08"],
      quarantineLabel: "Quarantined when it was burned",
    });
    expect(handover?.detail).toBe(
      `Cut-off ${clockTime(at(11))}, the first evidence. Six items finished before it count as done; two in progress or touched after it are quarantined for a person.`,
    );
  });

  it.each([
    ["pending", "A replacement is on its way"],
    ["needs_person", "Not replaced: it needs a person"],
  ] as const)("says when the replacement is %s", (status, headline) => {
    const instances = sampleInstances().map((item) =>
      item.session_id === "sess-w4"
        ? {
            ...item,
            replaced_by: null,
            replacement: { ...item.replacement!, status },
          }
        : item,
    );
    const { model } = setup(instances);
    expect(replacementOf(agentIn(model, "sess-w4"), model)?.headline).toBe(
      headline,
    );
  });

  it("says when a burn didn't ask for one", () => {
    const instances = sampleInstances().map((item) =>
      item.session_id === "sess-w4"
        ? { ...item, replaced_by: null, replacement: null }
        : item,
    );
    const { model } = setup(instances);
    const agent = agentIn(model, "sess-w4");
    expect(replacementOf(agent, model, ROLES[0])?.headline).toBe(
      "Not replaced",
    );
    expect(
      replacementOf(agent, model, { ...ROLES[0], replace_on_burn: false })
        ?.detail,
    ).toBe("The worker role doesn't replace burned agents.");
  });
});

describe("takeoverOf", () => {
  it("says where a replacement resumed, from its predecessor's record", () => {
    const { model } = setup();
    expect(takeoverOf(agentIn(model, "sess-w5"), model)).toMatchObject({
      headline: "Took over from worker-04",
      other: { key: "sess-w4" },
      detail: "Resumed at 6 of 8 items; 2 quarantined for a person.",
      quarantined: ["w4-07", "w4-08"],
      quarantineLabel: "Inherited quarantine",
    });
  });

  it("keeps a reported zero, and leaves out a count never reported", () => {
    const withStarted = (items_started: number | undefined) =>
      sampleInstances().map((item) =>
        item.session_id === "sess-w4"
          ? {
              ...item,
              items_started,
              replacement: { ...item.replacement!, done: [] },
            }
          : item,
      );
    const zero = setup(withStarted(0)).model;
    expect(takeoverOf(agentIn(zero, "sess-w5"), zero)?.detail).toBe(
      "Resumed at 0 of 0 items; 2 quarantined for a person.",
    );
    const none = setup(withStarted(undefined)).model;
    expect(takeoverOf(agentIn(none, "sess-w5"), none)?.detail).toBe(
      "Resumed at 0 items; 2 quarantined for a person.",
    );
  });

  it("says progress wasn't reported when its predecessor is gone", () => {
    const instances = sampleInstances().filter(
      (item) => item.session_id !== "sess-w4",
    );
    const { model } = setup(instances);
    expect(takeoverOf(agentIn(model, "sess-w5"), model)).toMatchObject({
      headline: "Took over from sess-w4",
      detail: "Resume progress not reported.",
      quarantined: [],
    });
  });

  it("is nothing for an agent that replaced no one", () => {
    const { model } = setup();
    expect(takeoverOf(agentIn(model, "sess-w2"), model)).toBeUndefined();
    expect(replacementOf(agentIn(model, "sess-w2"), model)).toMatchObject({
      headline: "Not replaced",
    });
  });
});

describe("blastSentence", () => {
  const key = "burn/agent-worker-4/aws_key";

  it("counts CloudTrail's calls by kind, newest time last", () => {
    const sentence = blastSentence({
      agent_id: "agent-worker-4",
      keys: [{ key_ref: key, status: "inactive" }],
      events: [
        { ts: at(5), key_ref: key, event_name: "InvokeModel" },
        { ts: at(4), key_ref: key, event_name: "Converse" },
        { ts: at(3), key_ref: key, event_name: "ListBuckets" },
      ],
    });
    expect(sentence).toBe(
      `CloudTrail shows 3 calls with the old aws_key, the last at ${clockTime(at(3))}: 2 model calls and 1 bucket list. ${CLOUDTRAIL_LAG}`,
    );
  });

  it("says when the calls were refused", () => {
    const sentence = blastSentence({
      agent_id: "agent-worker-4",
      keys: [{ key_ref: key, status: "inactive" }],
      events: [
        {
          ts: at(3),
          key_ref: key,
          event_name: "GetCallerIdentity",
          error_code: "InvalidClientTokenId",
        },
      ],
    });
    expect(sentence).toContain("1 identity check. All were refused.");
  });

  it("says when there is nothing to show", () => {
    expect(blastSentence({ agent_id: "a", keys: [], events: [] })).toBe(
      "It held no AWS keys, so CloudTrail has nothing to show.",
    );
    expect(
      blastSentence({ agent_id: "a", keys: [{ key_ref: key }], events: [] }),
    ).toBe(`CloudTrail shows no calls with its AWS key yet. ${CLOUDTRAIL_LAG}`);
  });
});
