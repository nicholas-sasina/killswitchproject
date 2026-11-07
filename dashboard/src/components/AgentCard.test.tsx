// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AgentCard } from "./AgentCard";
import type { AgentState, BurnResult } from "../contracts";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("highlights a completed Burn and shows its counts for ten seconds while keeping the agent revoked", async () => {
  vi.useFakeTimers();
  const agent: AgentState = {
    agent_id: `agent-${crypto.randomUUID()}`,
    status: "active",
    session_id: crypto.randomUUID(),
  };
  const result: BurnResult = {
    agent_id: agent.agent_id,
    revoked: true,
    sessions_revoked: [agent.session_id!],
    rotated: [`burn/${agent.agent_id}/api_key`],
    flagged: [],
  };
  const { container, rerender } = render(
    <AgentCard
      agent={agent}
      selected
      busy={false}
      onSelect={() => {}}
      onBurn={() => {}}
    />,
  );
  expect(screen.queryByRole("status")).toBeNull();
  rerender(
    <AgentCard
      agent={agent}
      result={result}
      selected
      busy={false}
      onSelect={() => {}}
      onBurn={() => {}}
    />,
  );
  expect(screen.getByRole("status").textContent).toContain(
    "Rotated 1 · Flagged 0",
  );
  expect(container.querySelector(".agent-card-recent")).toBeTruthy();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(9_999);
  });
  expect(screen.getByRole("status")).toBeTruthy();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.queryByRole("status")).toBeNull();
  expect(container.querySelector(".agent-card-recent")).toBeNull();
  expect(
    (
      screen.getByRole("button", {
        name: `Burn ${agent.agent_id}`,
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(screen.getByText("revoked")).toBeTruthy();
});
