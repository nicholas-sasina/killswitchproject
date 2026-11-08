// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { useMemo, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BurnApi } from "../../api";
import type {
  BurnResult,
  Instance,
  LedgerEntry,
  ScopedBurnResult,
} from "../../contracts";
import { ConsoleDataContext, type ConsoleData } from "../../data/ConsoleData";
import {
  makeConsole,
  NOW,
  sampleInstances,
  stubApi,
} from "../../features/burn/fixture";
import { clockTime } from "../../model/time";
import { AgentPage } from "./AgentPage";

function reduceMotion(reduce: boolean) {
  vi.stubGlobal(
    "matchMedia",
    (query: string) =>
      ({
        matches: reduce && query.includes("reduce"),
        addEventListener() {},
        removeEventListener() {},
      }) as unknown as MediaQueryList,
  );
}

beforeEach(() => reduceMotion(false));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const at = (minutesBefore: number) =>
  new Date(NOW.valueOf() - minutesBefore * 60_000).toISOString();

const LEDGER: LedgerEntry[] = [
  {
    secret_ref: "burn/shared/crm_db_url",
    fingerprint: "f1",
    secret_type: "other",
    owner: "shared",
    source: "tool_response",
    source_ref: "read_config:crm.yaml",
    first_seen: at(20),
    status: "active",
    exposure: "seen",
  },
  {
    secret_ref: "burn/agent-worker-3/api_key",
    fingerprint: "f2",
    secret_type: "api_key",
    owner: "agent",
    owner_agent_id: "agent-worker-3",
    source: "tool_response",
    first_seen: at(30),
    status: "active",
    exposure: "referenced",
  },
];

const RESULT: BurnResult = {
  agent_id: "agent-worker-3",
  revoked: true,
  sessions_revoked: ["sess-w3"],
  rotated: ["burn/agent-worker-3/api_key", "burn/agent-worker-3/aws_key"],
  flagged: ["burn/shared/crm_db_url"],
};

const ANSWER: ScopedBurnResult = {
  scope: "instance",
  id: "sess-w3",
  burned: [RESULT],
  failed: [],
};

function withLedger(entries = LEDGER, over: Partial<BurnApi> = {}): BurnApi {
  return stubApi({
    ledger: vi.fn(async (id: string) => ({ agent_id: id, entries })),
    ...over,
  } as Partial<BurnApi>);
}

function show(sessionId: string, data: ConsoleData = makeConsole()) {
  return render(
    <ConsoleDataContext value={data}>
      <AgentPage sessionId={sessionId} />
    </ConsoleDataContext>,
  );
}

/** The console as it behaves: a burn reported with recordBurn shows at once. */
function Live({ api, sessionId }: { api: BurnApi; sessionId: string }) {
  const [burned, setBurned] = useState<string[]>([]);
  const data = useMemo(
    () =>
      makeConsole({
        api,
        burnedSessions: burned,
        recordBurn: (results: BurnResult[]) =>
          setBurned(results.flatMap((result) => result.sessions_revoked)),
      }),
    [api, burned],
  );
  return (
    <ConsoleDataContext value={data}>
      <AgentPage sessionId={sessionId} />
    </ConsoleDataContext>
  );
}

const lede = () => document.querySelector(".hero-lede")?.textContent ?? "";

const items = (region: HTMLElement) =>
  within(region)
    .getAllByRole("listitem")
    .map((item) => item.textContent);

describe("AgentPage", () => {
  it("says it is loading before the agents arrive", () => {
    show("sess-w3", makeConsole({ instances: [], instancesLoaded: false }));
    expect(screen.getByText("Loading this agent…")).toBeTruthy();
  });

  it("says when no agent has the id", () => {
    show("sess-nobody");
    expect(screen.getByText(/No agent has this id/)).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "See every role" }).getAttribute("href"),
    ).toBe("#/roles");
  });

  it("shows a running agent: its keys, what a burn would do, who it is", async () => {
    show("sess-w3", makeConsole({ api: withLedger() }));
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "worker-03",
    );
    expect(screen.getByText("Running")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Burn worker-03" })).toBeTruthy();

    const ledger = await screen.findByRole("table", {
      name: "Keys worker-03 saw",
    });
    const rows = within(ledger)
      .getAllByRole("row")
      .slice(1)
      .map((row) => row.textContent);
    expect(rows).toEqual([
      "crm_db_urlread_config crm.yaml, sharedPlain textTracked",
      "api_keyits own copySeen as a referenceTracked",
    ]);
    expect(lede()).toContain("It saw one shared key in plain text.");

    const plan = screen.getByRole("region", { name: "If you burn it" });
    expect(items(plan)).toEqual([
      "Not done: Revoke its badgeIts next call is refused",
      "Not done: Rotate api_keyIts own copy; nobody else uses it",
      "Not done: Replace aws_keyThe old key goes inactive",
      "Not done: Flag crm_db_urlShared; its owner decides",
      "Not done: Leave everyone else running3 agents, including reader-01",
    ]);

    const identity = screen.getByRole("region", { name: "Identity" });
    expect(identity.textContent).toContain("Started bypriya@example.com");
    expect(identity.textContent).toContain("Join tokentok_8f2a");
    expect(
      within(identity)
        .getByRole("link", { name: "reader-01" })
        .getAttribute("href"),
    ).toBe("#/agents/sess-r1");
    expect(screen.queryByRole("region", { name: "After the burn" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Replacement" })).toBeNull();
  });

  it("says when its ledger can't be loaded", async () => {
    const api = stubApi({
      ledger: vi.fn(async () => {
        throw new Error("HTTP 503");
      }),
    } as Partial<BurnApi>);
    show("sess-w3", makeConsole({ api }));
    expect(
      (await screen.findByText(/Its ledger could not be loaded/)).textContent,
    ).toContain("HTTP 503");
  });

  it("says when it hasn't seen a key yet", async () => {
    show("sess-w2", makeConsole({ api: withLedger([]) }));
    expect(
      await screen.findByText(
        "No keys yet. Keys appear here as worker-02 reads them.",
      ),
    ).toBeTruthy();
  });

  it("asks before it burns, then ticks through what the burn did", async () => {
    const burnScope = vi.fn(async () => ANSWER);
    const api = withLedger(LEDGER, { burnScope } as Partial<BurnApi>);
    render(<Live api={api} sessionId="sess-w3" />);

    fireEvent.click(screen.getByRole("button", { name: "Burn worker-03" }));
    const dialog = screen.getByRole("dialog", { name: "Burn worker-03?" });
    await waitFor(() =>
      expect(dialog.textContent).toContain("Flag crm_db_url"),
    );
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Burn worker-03" }),
    );

    const contained = await screen.findByRole("region", {
      name: /^Contained at /,
    });
    expect(burnScope).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "instance", id: "sess-w3" }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("Burned")).toBeTruthy();
    expect(document.activeElement?.className).toContain("agent-containment");
    // It ticks: not everything is done at first, then everything is.
    expect(items(contained).some((text) => text?.startsWith("Not done"))).toBe(
      true,
    );
    await waitFor(
      () =>
        expect(items(contained)).toEqual([
          "Done: Badge revokedIts next call is refused",
          "Done: api_key rotatedThe old value no longer works",
          "Done: aws_key replacedThe old key is inactive",
          "Done: crm_db_url sent to its ownerShared; its owner decides whether to rotate",
          "Done: 3 agents untouchedIncluding its child, reader-01",
        ]),
      { timeout: 4000 },
    );
    expect(lede()).toContain("Burned by you at");
  });

  it("shows a burned agent: why, what the burn did, who took over", () => {
    show("sess-w4");
    expect(screen.getByText("Burned")).toBeTruthy();
    expect(lede()).toContain(
      `Burned by priya@example.com at ${clockTime(at(10))}: read an unverified vendor page.`,
    );
    expect(screen.queryByRole("button", { name: /^Burn / })).toBeNull();
    expect(
      screen.getByRole("button", { name: "Replay the burn" }),
    ).toBeTruthy();

    const contained = screen.getByRole("region", {
      name: `Contained at ${clockTime(at(10))}`,
    });
    expect(items(contained)).toEqual([
      "Done: Badge revokedIts next call is refused",
      "Done: api_key rotatedThe old value no longer works",
      "Done: aws_key replacedThe old key is inactive",
      "Done: crm_db_url sent to its ownerShared; its owner decides whether to rotate",
      "Done: Every other agent untouchedA burn stops only this agent",
    ]);

    const after = screen.getByRole("region", { name: "After the burn" });
    expect(after.textContent).toContain("worker-05 took over");
    expect(after.textContent).toContain(
      "Six items finished before it count as done; two in progress or touched after it are quarantined for a person.",
    );
    expect(within(after).getByText("w4-07")).toBeTruthy();
    expect(within(after).queryByText("w4-01")).toBeNull();
    expect(within(after).getByText("Blast radius")).toBeTruthy();
  });

  it("replays the burn on request, and never under reduced motion", () => {
    show("sess-w4");
    fireEvent.click(screen.getByRole("button", { name: "Replay the burn" }));
    const contained = screen.getByRole("region", { name: /^Contained at / });
    expect(items(contained)[1]).toMatch(/^Not done: /);
    cleanup();

    reduceMotion(true);
    show("sess-w4");
    expect(
      screen.queryByRole("button", { name: "Replay the burn" }),
    ).toBeNull();
  });

  it("shows every step done at once after a burn under reduced motion", async () => {
    reduceMotion(true);
    const burnScope = vi.fn(async () => ANSWER);
    render(
      <Live
        api={withLedger(LEDGER, { burnScope } as Partial<BurnApi>)}
        sessionId="sess-w3"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Burn worker-03" }));
    const dialog = screen.getByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Burn worker-03" }),
    );
    const contained = await screen.findByRole("region", {
      name: /^Contained at /,
    });
    expect(items(contained).every((text) => text?.startsWith("Done: "))).toBe(
      true,
    );
  });

  it("says it finished on its own, with nothing to burn", () => {
    show("sess-w1");
    expect(screen.getByRole("region", { name: "Finished" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Burn / })).toBeNull();
  });

  it("says a scenario agent that never started a session has nothing to burn", () => {
    show(
      "agent-alpha",
      makeConsole({
        instances: [],
        events: [],
        agents: [{ agent_id: "agent-alpha", role: "alpha", status: "idle" }],
      }),
    );
    expect(
      screen.getByRole("region", { name: "Finished" }).textContent,
    ).toContain(
      "alpha-01 has not started a session, so there is nothing to burn.",
    );
    expect(screen.queryByRole("button", { name: /^Burn / })).toBeNull();
  });
});

describe("AgentPage for a replacement", () => {
  const withOld = (over: Partial<Instance>) =>
    sampleInstances().map((item) =>
      item.session_id === "sess-w4" ? { ...item, ...over } : item,
    );

  it("says what it took over and the quarantine it inherited", () => {
    show("sess-w5");
    const card = screen.getByRole("region", { name: "Replacement" });
    expect(
      within(card)
        .getByRole("link", { name: "worker-04" })
        .getAttribute("href"),
    ).toBe("#/agents/sess-w4");
    expect(card.textContent).toContain(
      "Resumed at 6 of 8 items; 2 quarantined for a person.",
    );
    expect(within(card).getByText("Inherited quarantine")).toBeTruthy();
    expect(within(card).getByText("w4-08")).toBeTruthy();
    expect(within(card).queryByText("w4-01")).toBeNull();
  });

  it("keeps a reported zero started count", () => {
    show(
      "sess-w5",
      makeConsole({
        instances: withOld({
          items_started: 0,
          replacement: { ...sampleInstances()[4].replacement!, done: [] },
        }),
      }),
    );
    expect(
      screen.getByRole("region", { name: "Replacement" }).textContent,
    ).toContain("Resumed at 0 of 0 items");
  });

  it("says progress wasn't reported when its predecessor is gone", () => {
    show(
      "sess-w5",
      makeConsole({
        instances: sampleInstances().filter(
          (item) => item.session_id !== "sess-w4",
        ),
      }),
    );
    const card = screen.getByRole("region", { name: "Replacement" });
    expect(card.textContent).toContain("Took over from sess-w4");
    expect(card.textContent).toContain("Resume progress not reported.");
  });

  it.each([
    ["pending", "A replacement is on its way"],
    ["needs_person", "Not replaced: it needs a person"],
  ] as const)("says when its replacement is %s", (status, headline) => {
    show(
      "sess-w4",
      makeConsole({
        instances: withOld({
          replaced_by: null,
          replacement: { ...sampleInstances()[4].replacement!, status },
        }),
      }),
    );
    expect(
      screen.getByRole("region", { name: "After the burn" }).textContent,
    ).toContain(headline);
  });
});
