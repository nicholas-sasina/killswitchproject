// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { LedgerEvent } from "../contracts";
import { AlertsView } from "./AlertsView";

afterEach(cleanup);

function blocks(count: number): LedgerEvent[] {
  return Array.from({ length: count }, (_, index) => ({
    event_id: `evt_${String(index).padStart(5, "0")}`,
    ts: "2026-11-08T05:00:00.000Z",
    agent_id: "agent-worker-a1b2",
    source: "egress",
    action: "blocked",
    detail: "destination_not_allowed",
  })) as LedgerEvent[];
}

describe("AlertsView", () => {
  it("draws a bounded number of rows and can show them all", () => {
    render(<AlertsView events={blocks(100)} />);
    const rows = () => screen.getAllByRole("listitem").length;
    expect(screen.getByText("100 alerts")).toBeTruthy();
    expect(rows()).toBe(60);
    fireEvent.click(screen.getByRole("button", { name: "Show all 100" }));
    expect(rows()).toBe(100);
    fireEvent.click(screen.getByRole("button", { name: "Show first 60" }));
    expect(rows()).toBe(60);
  });

  it("shows no toggle for a short list", () => {
    render(<AlertsView events={blocks(5)} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(5);
    expect(screen.queryByRole("button", { name: /Show all/ })).toBeNull();
  });
});
