// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BurnApi } from "../api";
import type { Role } from "../contracts";
import type { FleetAgent } from "../fleet";
import { BurnedView } from "./BurnedView";

beforeEach(() => {
  vi.useFakeTimers();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function burned(agentId: string, role: string): FleetAgent {
  return {
    key: `sess_${agentId}`,
    agentId,
    role,
    status: "revoked",
    sessionId: `sess_${agentId}`,
    from: "instance",
  };
}

function fakeApi(roles: Role[]) {
  const api = {
    roles: vi.fn(async () => ({ roles })),
    ledger: vi.fn(async (id: string) => ({ agent_id: id, entries: [] })),
    blastRadius: vi.fn(async (id: string) => ({
      agent_id: id,
      keys: [],
      events: [],
    })),
    createRole: vi.fn(async (spec: Role) => ({ ...spec, managed_by: "ui" })),
    createToken: vi.fn(async (role: string) => ({
      token_id: "t1",
      role,
      uses: 1,
      uses_left: 1,
      expires_at: "2026-11-08T06:00:00Z",
      status: "active",
      token: "bjt_t1.secret",
    })),
  };
  return { api, typed: api as unknown as BurnApi };
}

async function show(fleet: FleetAgent[], roles: Role[]) {
  const fixture = fakeApi(roles);
  await act(async () => {
    render(
      <BurnedView
        api={fixture.typed}
        fleet={fleet}
        events={[]}
        instances={[]}
        enabled
      />,
    );
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  return fixture.api;
}

describe("rebuilding a burned agent", () => {
  it("offers no join token for a scenario agent, only a copy", async () => {
    await show(
      [burned("agent-reader", "reader")],
      [{ name: "reader", managed_by: "scenario", tools: ["read_ticket"] }],
    );
    expect(
      screen.queryByRole("button", { name: "Re-add with the same setup" }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Copy setup to a new role" }),
    ).toBeTruthy();
    expect(screen.getByText(/would refuse a join token/)).toBeTruthy();
  });

  it("offers a join token for a role the dashboard manages", async () => {
    await show(
      [burned("agent-worker-a1b2", "worker")],
      [{ name: "worker", managed_by: "ui", tools: [] }],
    );
    expect(
      screen.getByRole("button", { name: "Re-add with the same setup" }),
    ).toBeTruthy();
  });

  it("copies every setting, references included, under a free name that fits", async () => {
    const long = "a-role-name-that-is-thirty-two-c";
    expect(long).toHaveLength(32);
    const api = await show(
      [burned(`agent-${long}-a1b2`, long)],
      [
        {
          name: long,
          managed_by: "ui",
          tools: ["read_ticket"],
          references: "report",
          replace_on_burn: true,
        },
        {
          name: "a-role-name-that-is-thirty-two-2",
          managed_by: "ui",
          tools: [],
        },
      ],
    );
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Copy setup to a new role" }),
      );
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Create role and token" }),
      );
    });
    expect(api.createRole).toHaveBeenCalledTimes(1);
    const spec = api.createRole.mock.calls[0][0];
    expect(spec.name).toBe("a-role-name-that-is-thirty-two-3");
    expect(spec.name.length).toBeLessThanOrEqual(32);
    expect(spec.references).toBe("report");
    expect(spec.replace_on_burn).toBe(true);
    expect(spec.tools).toEqual(["read_ticket"]);
  });
});
