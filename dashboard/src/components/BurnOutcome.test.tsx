// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { BurnOutcome } from "./BurnOutcome";

afterEach(cleanup);

it.each([
  [0, "0 sessions revoked"],
  [1, "1 session revoked"],
  [2, "2 sessions revoked"],
] as const)("pluralizes the result for %s revoked sessions", (count, text) => {
  render(
    <BurnOutcome
      result={{
        agent_id: "agent-worker",
        revoked: true,
        sessions_revoked: Array.from(
          { length: count },
          (_, index) => `session_${index}`,
        ),
        rotated: [],
        flagged: [],
      }}
    />,
  );
  expect(screen.getByText(text)).toBeTruthy();
});
