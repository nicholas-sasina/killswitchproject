// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, type BurnApi } from "../../api";
import type { BurnResult, LedgerEntry } from "../../contracts";
import { ConsoleDataContext, type ConsoleData } from "../../data/ConsoleData";
import { BurnDialog } from "./BurnDialog";
import { makeConsole, stubApi } from "./fixture";
import type { BurnOutcome, BurnTarget } from "./types";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const LEDGER: LedgerEntry[] = [
  {
    secret_ref: "burn/shared/crm_db_url",
    fingerprint: "f1",
    secret_type: "other",
    owner: "shared",
    source: "tool_response",
    via: "read_config",
    first_seen: "2026-11-08T19:20:00Z",
    status: "active",
    exposure: "seen",
  },
];

const RESULT: BurnResult = {
  agent_id: "agent-worker-3",
  revoked: true,
  sessions_revoked: ["sess-w3"],
  rotated: ["burn/agent-worker-3/api_key", "burn/agent-worker-3/aws_key"],
  flagged: ["burn/shared/crm_db_url"],
};

function open(
  data: ConsoleData,
  target: (data: ConsoleData) => BurnTarget,
  props: { onClose?: () => void; onBurned?: (o: BurnOutcome) => void } = {},
) {
  const onClose = props.onClose ?? vi.fn();
  const view = render(
    <ConsoleDataContext value={data}>
      <BurnDialog target={target(data)} onClose={onClose} {...props} />
    </ConsoleDataContext>,
  );
  return { ...view, onClose };
}

const worker3 = (data: ConsoleData): BurnTarget => ({
  scope: "agent",
  agent: data.model.byKey.get("sess-w3")!,
});

describe("BurnDialog for one agent", () => {
  it("lists exactly what will happen from its ledger, role and children", async () => {
    const api = stubApi({
      ledger: vi.fn(async (id: string) => ({ agent_id: id, entries: LEDGER })),
    } as Partial<BurnApi>);
    open(makeConsole({ api }), worker3);
    const dialog = screen.getByRole("dialog", { name: "Burn worker-03?" });
    expect(within(dialog).getByText("Burn one agent")).toBeTruthy();
    await waitFor(() =>
      expect(dialog.textContent).toContain(
        "Flag crm_db_url, which it saw in plain text, for its owner.",
      ),
    );
    const items = within(dialog)
      .getAllByRole("listitem")
      .map((item) => item.textContent);
    expect(items).toEqual([
      "Revoke its badge. Its next call is refused.",
      "Rotate api_key and aws_key, the two keys it owns. The old values stop working at once.",
      "Flag crm_db_url, which it saw in plain text, for its owner.",
      "Leave its child reader-01 running, and every other agent.",
      "Mark it for a replacement. A fresh worker resumes the work it finished; anything it touched after the first evidence waits for a person.",
    ]);
    expect(api.ledger).toHaveBeenCalledWith(
      "agent-worker-3",
      expect.any(AbortSignal),
    );
  });

  it("starts on Cancel, and Cancel closes it", () => {
    const { onClose } = open(makeConsole(), worker3);
    const cancel = screen.getByRole("button", { name: "Cancel" });
    expect(document.activeElement).toBe(cancel);
    fireEvent.click(cancel);
    expect(onClose).toHaveBeenCalled();
  });

  it("burns the one session with the reason, records it, then reports it", async () => {
    const burnScope = vi.fn(async () => ({
      scope: "instance",
      id: "sess-w3",
      burned: [RESULT],
    }));
    const data = makeConsole({
      api: stubApi({ burnScope } as Partial<BurnApi>),
    });
    const onBurned = vi.fn();
    open(data, worker3, { onBurned });
    fireEvent.change(screen.getByLabelText(/Reason/), {
      target: { value: "  read an unverified vendor page " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Burn worker-03" }));
    await waitFor(() => expect(onBurned).toHaveBeenCalled());
    expect(burnScope).toHaveBeenCalledWith({
      scope: "instance",
      id: "sess-w3",
      reason: "read an unverified vendor page",
    });
    expect(data.recordBurn).toHaveBeenCalledWith([RESULT]);
    const outcome = onBurned.mock.calls[0][0] as BurnOutcome;
    expect(outcome).toMatchObject({
      burned: [RESULT],
      failed: [],
      reason: "read an unverified vendor page",
    });
    expect(
      (data.recordBurn as ReturnType<typeof vi.fn>).mock.invocationCallOrder[0],
    ).toBeLessThan(onBurned.mock.invocationCallOrder[0]);
  });

  it("ticks the checklist in place when the page keeps it open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const burnScope = vi.fn(async () => ({
      scope: "instance",
      id: "sess-w3",
      burned: [RESULT],
    }));
    open(
      makeConsole({ api: stubApi({ burnScope } as Partial<BurnApi>) }),
      worker3,
    );
    fireEvent.click(screen.getByRole("button", { name: "Burn worker-03" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Burned worker-03",
    });
    await waitFor(() =>
      expect(document.activeElement).toBe(
        within(dialog).getByRole("button", { name: "Done" }),
      ),
    );
    await act(async () => vi.advanceTimersByTime(450 * 6));
    expect(dialog.textContent).toContain("aws_key replaced");
    expect(dialog.textContent).toContain("Including its child, reader-01");
    // worker-02, worker-05 and reader-01 keep running.
    expect(dialog.textContent).toContain("3 agents untouched");
  });

  it("keeps the dialog open with a plain error when the request fails", async () => {
    const burnScope = vi.fn(async () => {
      throw new ApiError("API request failed (HTTP 500).", 500);
    });
    const data = makeConsole({
      api: stubApi({ burnScope } as Partial<BurnApi>),
    });
    const onBurned = vi.fn();
    open(data, worker3, { onBurned });
    fireEvent.click(screen.getByRole("button", { name: "Burn worker-03" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "API request failed (HTTP 500). Outcome not confirmed. Check worker-03's status before retrying.",
    );
    expect(
      screen.getByRole("dialog", { name: "Burn worker-03?" }),
    ).toBeTruthy();
    expect(onBurned).not.toHaveBeenCalled();
    expect(data.recordBurn).not.toHaveBeenCalled();
    expect(
      (
        screen.getByRole("button", {
          name: "Burn worker-03",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
  });

  it("says what a 404 means", async () => {
    const burnScope = vi.fn(async () => {
      throw new ApiError("API request failed (HTTP 404).", 404);
    });
    open(
      makeConsole({ api: stubApi({ burnScope } as Partial<BurnApi>) }),
      worker3,
    );
    fireEvent.click(screen.getByRole("button", { name: "Burn worker-03" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /^Burn couldn't find worker-03 \(HTTP 404\)/,
    );
  });

  it("burns a scenario agent with no session by its agent id", async () => {
    const burn = vi.fn(async () => ({ ...RESULT, agent_id: "agent-alpha" }));
    const data = makeConsole({
      instances: [],
      events: [],
      agents: [{ agent_id: "agent-alpha", role: "alpha", status: "idle" }],
      api: stubApi({ burn } as Partial<BurnApi>),
    });
    const onBurned = vi.fn();
    open(
      data,
      (d) => ({ scope: "agent", agent: d.model.byKey.get("agent-alpha")! }),
      { onBurned },
    );
    fireEvent.click(screen.getByRole("button", { name: "Burn alpha-01" }));
    await waitFor(() => expect(onBurned).toHaveBeenCalled());
    expect(burn).toHaveBeenCalledWith({ agent_id: "agent-alpha" });
  });
});

describe("BurnDialog for a role", () => {
  it("lists what burning every running agent does and burns the role", async () => {
    const burnScope = vi.fn(async () => ({
      scope: "role",
      id: "worker",
      burned: [RESULT],
      failed: [{ agent_id: "agent-worker-2", error: "timed out" }],
    }));
    const onBurned = vi.fn();
    open(
      makeConsole({ api: stubApi({ burnScope } as Partial<BurnApi>) }),
      () => ({ scope: "role", role: "worker" }),
      { onBurned },
    );
    const dialog = screen.getByRole("dialog", { name: "Burn every worker?" });
    expect(within(dialog).getByText("Burn a role")).toBeTruthy();
    expect(dialog.textContent).toContain(
      "Revoke the badges of all 3 running workers.",
    );
    expect(dialog.textContent).toContain("Leave reader-01 running");
    fireEvent.click(screen.getByRole("button", { name: "Burn every worker" }));
    await waitFor(() => expect(onBurned).toHaveBeenCalled());
    expect(burnScope).toHaveBeenCalledWith({ scope: "role", id: "worker" });
    expect(onBurned.mock.calls[0][0].failed).toEqual([
      { agent_id: "agent-worker-2", error: "timed out" },
    ]);
  });

  it("offers nothing to burn when no agent of the role runs", () => {
    open(makeConsole({ instances: [] }), () => ({
      scope: "role",
      role: "worker",
    }));
    expect(
      screen.getByText("No workers are running, so there is nothing to burn."),
    ).toBeTruthy();
    expect(
      (
        screen.getByRole("button", {
          name: "Burn every worker",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(screen.queryByLabelText(/Reason/)).toBeNull();
  });
});
