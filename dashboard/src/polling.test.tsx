// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createApi } from "./api";
import { HISTORY_PAGE, useEventFeed } from "./polling";
import { eventIdAt } from "./events";
import type { EventsResponse } from "./contracts";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("event polling", () => {
  it("polls once per second, preserves the cursor after empty polls and stops on unmount", async () => {
    vi.useFakeTimers();
    const cursor = `evt_${String(1).padStart(26, "0")}`;
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ events: [], agents: [], cursor })),
      );
    // Responses are consumed once, just as they are by the browser.
    fetcher.mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            events: [],
            agents: [],
            cursor: fetcher.mock.calls.length === 1 ? cursor : null,
          }),
        ),
    );
    const api = createApi(
      {
        burnUrl: "https://burn.example.test",
        localDev: true,
        demoToken: crypto.randomUUID(),
      },
      () => {},
      fetcher,
    );
    const { result, unmount } = renderHook(() => useEventFeed(api, true));
    await act(async () => {});
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(999);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(String(fetcher.mock.calls[1][0])).toContain(`?since=${cursor}`);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(String(fetcher.mock.calls[2][0])).toContain(`?since=${cursor}`);
    expect(result.current.state.cursor).toBe(cursor);
    unmount();
    await vi.advanceTimersByTimeAsync(2_000);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("does not overlap a slow request", async () => {
    vi.useFakeTimers();
    let resolve!: (value: EventsResponse) => void;
    const api = createApi(
      { burnUrl: "", localDev: true, demoToken: "" },
      () => {},
    );
    api.events = vi.fn().mockImplementation(
      () =>
        new Promise<EventsResponse>((done) => {
          resolve = done;
        }),
    );
    const { unmount } = renderHook(() => useEventFeed(api, true));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(api.events).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolve({ events: [], agents: [] });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(api.events).toHaveBeenCalledTimes(2);
    unmount();
  });

  it("pages history forward from a moment until it meets the live events", async () => {
    vi.useFakeTimers();
    const id = (n: number) => `evt_Z${String(n).padStart(25, "0")}`;
    const page = (from: number, count: number) =>
      Array.from({ length: count }, (_, i) => ({
        event_id: id(from + i),
        ts: new Date(1_000 + from + i).toISOString(),
        agent_id: "agent-reader",
        source: "tool_response" as const,
        action: "recorded" as const,
      }));
    const api = createApi(
      { burnUrl: "", localDev: true, demoToken: "" },
      () => {},
    );
    // The live poll's first page is a full 200, newest last.
    const live = page(5_000, 200);
    api.events = vi.fn(async (since?: string, _signal?: AbortSignal) => {
      if (!since) return { events: live, agents: [] };
      if (since === eventIdAt(42))
        return { events: page(0, HISTORY_PAGE), agents: [] };
      if (since === id(HISTORY_PAGE - 1))
        return { events: page(HISTORY_PAGE, 10), agents: [] };
      return { events: [], agents: [] };
    });
    const { result, unmount } = renderHook(() =>
      useEventFeed(api, true, { historySince: 42 }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const calls = vi.mocked(api.events).mock.calls;
    expect(calls.slice(0, 3).map(([since, , limit]) => [since, limit])).toEqual(
      [
        [undefined, undefined],
        [eventIdAt(42), HISTORY_PAGE],
        [id(HISTORY_PAGE - 1), HISTORY_PAGE],
      ],
    );
    expect(Object.keys(result.current.state.byId)).toHaveLength(
      200 + HISTORY_PAGE + 10,
    );
    expect(result.current.history).toEqual({
      from: new Date(42).toISOString(),
      complete: true,
    });
    // The live cursor is the newest live event, not the history's.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(calls.at(-1)?.[0]).toBe(live.at(-1)?.event_id);
    unmount();
  });

  it("calls a short first page the whole history", async () => {
    vi.useFakeTimers();
    const api = createApi(
      { burnUrl: "", localDev: true, demoToken: "" },
      () => {},
    );
    api.events = vi.fn(async () => ({ events: [], agents: [] }));
    const { result, unmount } = renderHook(() =>
      useEventFeed(api, true, { historySince: 42 }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(api.events).toHaveBeenCalledTimes(1);
    expect(result.current.history.complete).toBe(true);
    unmount();
  });
});
