// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { App } from "./App";

beforeAll(() => {
  // jsdom has no layout, canvas or media queries; the page must cope without them.
  HTMLCanvasElement.prototype.getContext = (() =>
    null) as unknown as HTMLCanvasElement["getContext"];
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
});

afterEach(cleanup);

describe("App", () => {
  it("renders every section in order, with real headings", () => {
    render(<App />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "One agent goes rogue. The other nineteen don’t.",
    );
    const titles = screen
      .getAllByRole("heading", { level: 2 })
      .map((h) => h.textContent);
    expect(titles).toEqual([
      "Interactive scenario/, Incident 01",
      "From a leaked credential to selective containment.",
      "Why not just revoke it?",
      "Built for agents that come and go",
      "What Burn won’t catch",
      "What each AWS service does",
      "Priced per tool call",
      "Burn one agent, not the whole swarm.",
    ]);
  });

  it("sends every Sign in link to the dashboard", () => {
    render(<App />);
    const links = screen.getAllByRole("link", { name: "Sign in" });
    expect(links).toHaveLength(2);
    for (const link of links)
      expect(link.getAttribute("href")).toBe("http://localhost:5173/");
  });

  it("jumps the hero to a step and pauses", () => {
    render(<App />);
    const step5 = screen.getByRole("button", {
      name: "Step 5: worker-07 burned. Nineteen keep running.",
    });
    act(() => fireEvent.click(step5));
    expect(step5.getAttribute("aria-current")).toBe("step");
    expect(screen.getByText("19 running, 1 burned")).toBeTruthy();
    expect(
      screen.getAllByRole("button", { name: "Play" }).length,
    ).toBeGreaterThan(0);
  });

  it("stops the pipeline on a step's finished state", () => {
    render(<App />);
    const step4 = screen.getByRole("button", {
      name: "Step 4: Burn contains that worker only.",
    });
    act(() => fireEvent.click(step4));
    expect(screen.getByText("Step 4 of 6")).toBeTruthy();
    expect(screen.getByText("Burn contains that worker only.")).toBeTruthy();
  });

  it("prices calls with the calculator", () => {
    render(<App />);
    act(() =>
      fireEvent.click(screen.getByRole("button", { name: "250,000 a day" })),
    );
    expect(screen.getByText("$375")).toBeTruthy();
  });
});
