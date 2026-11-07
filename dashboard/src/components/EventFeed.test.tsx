// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { EventFeed } from "./EventFeed";
import type { LedgerEvent } from "../contracts";

afterEach(cleanup);

it("renders a canary_used event from the provider instead of blanking the feed", () => {
  const event: LedgerEvent = {
    event_id: "evt_01JZ0000000000000000000000",
    ts: "2026-11-07T20:00:00.000Z",
    agent_id: "agent-worker-3fa91c",
    session_id: "sess_0123456789abcdef",
    source: "provider",
    action: "canary_used",
    secret_ref: "burn/agent-worker-3fa91c/canary_0f1e2d3c4b5a69788796a5b4",
    detail:
      "canary of session sess_0123456789abcdef presented at the provider; rejected",
  };
  render(<EventFeed events={[event]} />);
  expect(screen.getByText("Canary credential used")).toBeTruthy();
  expect(screen.getByText(event.secret_ref!)).toBeTruthy();
});
