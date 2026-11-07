// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createApi } from "./api";
import { useEventFeed } from "./polling";
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
    const api = createApi({ burnUrl: "", demoToken: "" }, () => {});
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
});
