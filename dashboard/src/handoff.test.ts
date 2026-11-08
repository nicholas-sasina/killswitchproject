// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { HANDOFF_CLASS, canHandOff, handOff } from "./handoff";

function stubMotion(reduce: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: reduce && query.includes("reduce"),
    addEventListener() {},
    removeEventListener() {},
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(document, "startViewTransition");
  document.documentElement.classList.remove(HANDOFF_CLASS);
});

describe("handOff", () => {
  it("swaps at once where the browser has no view transitions", () => {
    const update = vi.fn();
    expect(canHandOff()).toBe(false);
    handOff(update);
    expect(update).toHaveBeenCalledOnce();
  });

  it("swaps at once when reduced motion is on", () => {
    stubMotion(true);
    const start = vi.fn();
    Object.assign(document, { startViewTransition: start });
    const update = vi.fn();
    handOff(update);
    expect(update).toHaveBeenCalledOnce();
    expect(start).not.toHaveBeenCalled();
  });

  it("runs the swap inside a view transition and names the brand only meanwhile", async () => {
    stubMotion(false);
    let finish = () => {};
    const finished = new Promise<void>((resolve) => (finish = resolve));
    const start = vi.fn((callback: () => void) => {
      expect(document.documentElement.classList.contains(HANDOFF_CLASS)).toBe(
        true,
      );
      callback();
      return { finished };
    });
    Object.assign(document, { startViewTransition: start });
    const update = vi.fn();
    handOff(update);
    expect(start).toHaveBeenCalledOnce();
    expect(update).toHaveBeenCalledOnce();
    finish();
    await finished;
    await Promise.resolve();
    expect(document.documentElement.classList.contains(HANDOFF_CLASS)).toBe(
      false,
    );
  });
});
