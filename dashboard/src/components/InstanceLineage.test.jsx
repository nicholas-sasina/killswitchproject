// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { InstanceLineage } from "./InstanceLineage";

afterEach(cleanup);

it("keeps table burn buttons at their natural width without changing agent card buttons", () => {
  const baseStyles = readFileSync("src/styles.css", "utf8");
  const swarmStyles = readFileSync("src/components/swarm.css", "utf8");
  const parent = {
    session_id: "session_parent",
    agent_id: "agent-planner-parent",
    role: "planner",
    status: "active",
    created_at: "2026-11-07T12:00:00Z",
  };
  render(
    <>
      <style>{`${baseStyles}\n${swarmStyles}`}</style>
      <button className="burn-button">Agent card action</button>
      <InstanceLineage
        selected={[parent]}
        instances={[parent]}
        disabled={false}
        onBurn={() => {}}
      />
    </>,
  );
  const burn = screen.getByRole("button", {
    name: "Burn instance agent-planner-parent",
  });
  expect(window.getComputedStyle(burn).width).toBe("auto");
  expect(window.getComputedStyle(burn).display).toBe("inline-flex");
  expect(
    window.getComputedStyle(
      screen.getByRole("button", { name: "Agent card action" }),
    ).width,
  ).toBe("100%");
});
