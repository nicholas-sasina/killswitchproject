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

it("renders an owner's dismissal of a flagged credential", () => {
  const event: LedgerEvent = {
    event_id: "evt_01JZ0000000000000000000001",
    ts: "2026-11-07T20:01:00.000Z",
    agent_id: "system",
    source: "burn",
    action: "dismissed",
    secret_ref: "burn/shared/crm_db_url",
    flag_level: "attempted",
    detail: "dismissed by sec-team: test fixture",
  };
  render(<EventFeed events={[event]} />);
  expect(screen.getByText("Flag dismissed by owner")).toBeTruthy();
});

it.each(["person@example.test", "instance:session_123", "system", null])(
  "shows nullable actor %s in the existing event attribution",
  (actor) => {
    render(
      <EventFeed
        events={[
          {
            event_id: "evt_1",
            ts: "2026-11-07T12:00:00Z",
            agent_id: "agent-worker",
            source: "burn",
            action: "revoked",
            actor,
          },
        ]}
      />,
    );
    const feed = screen.getByRole("region", { name: "Live events" });
    expect(feed.textContent).toContain("agent-worker");
    expect(feed.textContent).toContain("burn");
    if (actor) expect(feed.textContent).toContain(` · ${actor}`);
    else expect(feed.textContent).not.toMatch(/null|undefined/);
  },
);
