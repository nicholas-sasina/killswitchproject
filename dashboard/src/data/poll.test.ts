// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSharedPoll } from "./poll";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("a shared poll", () => {
  it("keeps the same data when an answer brings nothing new, so nothing re-renders", async () => {
    let answer = { roles: [{ name: "worker" }] };
    const load = vi.fn(async () => structuredClone(answer));
    const { result } = renderHook(() => useSharedPoll(load, 5_000));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const first = result.current.data;
    expect(first).toEqual(answer);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(load).toHaveBeenCalledTimes(2);
    expect(result.current.data).toBe(first);

    answer = { roles: [{ name: "worker" }, { name: "reader" }] };
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(result.current.data).not.toBe(first);
    expect(result.current.data).toEqual(answer);
  });
});
