// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ContainmentChecklist, TICK_MS } from "./Checklist";
import type { ContainmentStep } from "./types";

const STEPS: ContainmentStep[] = [
  {
    id: "revoked",
    title: "Badge revoked",
    sub: "Its next call is refused",
    hot: true,
  },
  { id: "api", title: "api_key rotated" },
  { id: "unfinished", title: "Keys not rotated", error: "Burn it again." },
];

function states() {
  return screen
    .getAllByRole("listitem")
    .map((item) => item.className.match(/is-(todo|done|error)/)?.[1]);
}

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
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("ContainmentChecklist", () => {
  it("shows what a burn will do, greyed, before it", () => {
    render(<ContainmentChecklist steps={STEPS} done={0} />);
    expect(states()).toEqual(["todo", "todo", "todo"]);
    expect(screen.getAllByText(/^Not done:/)).toHaveLength(3);
  });

  it("shows a past burn done at once, with what didn't finish", () => {
    render(<ContainmentChecklist steps={STEPS} done={STEPS.length} />);
    expect(states()).toEqual(["done", "done", "error"]);
    expect(screen.getByText("Burn it again.")).toBeTruthy();
    expect(screen.getByText(/^Didn't finish:/)).toBeTruthy();
  });

  it("ticks one step about every 450 ms, in order, and announces each", () => {
    vi.useFakeTimers();
    render(<ContainmentChecklist steps={STEPS} done={0} animate />);
    expect(states()).toEqual(["todo", "todo", "todo"]);
    act(() => vi.advanceTimersByTime(TICK_MS));
    expect(states()).toEqual(["done", "todo", "todo"]);
    expect(screen.getByText("Badge revoked. 1 of 3 done.")).toBeTruthy();
    act(() => vi.advanceTimersByTime(TICK_MS));
    expect(states()).toEqual(["done", "done", "todo"]);
    act(() => vi.advanceTimersByTime(TICK_MS));
    expect(states()).toEqual(["done", "done", "error"]);
    expect(
      screen.getByText("Keys not rotated: Burn it again. 3 of 3 done."),
    ).toBeTruthy();
    act(() => vi.advanceTimersByTime(TICK_MS * 4));
    expect(states()).toEqual(["done", "done", "error"]);
  });

  it("shows the final state at once with reduced motion", () => {
    reduceMotion(true);
    vi.useFakeTimers();
    const { container } = render(
      <ContainmentChecklist steps={STEPS} done={0} animate />,
    );
    expect(states()).toEqual(["done", "done", "error"]);
    expect(container.querySelector(".is-ticking")).toBeNull();
  });
});
