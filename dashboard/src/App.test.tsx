// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

beforeEach(() => {
  vi.useFakeTimers();
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("containment dashboard", () => {
  it("requires confirmation before Burn, shows its real result and loads the timeline", async () => {
    const id = `agent-${crypto.randomUUID()}`;
    const ref = `burn/${id}/api_key`;
    let session = crypto.randomUUID();
    let revoked = false;
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async (input, init) => {
        const path = new URL(String(input)).pathname;
        let body: unknown;
        if (path === "/events")
          body = {
            events: [],
            agents: [
              {
                agent_id: id,
                role: "Reader",
                status: revoked ? "revoked" : "active",
                session_id: session,
                exposures: 1,
              },
            ],
          };
        else if (path.endsWith("/ledger")) body = { agent_id: id, entries: [] };
        else if (path.endsWith("/blast-radius"))
          body = { agent_id: id, keys: [], events: [] };
        else if (path === "/encodings") body = { encodings: ["plain"] };
        else if (path === "/burn") {
          expect(init?.method).toBe("POST");
          expect(JSON.parse(String(init?.body))).toEqual({
            agent_id: id,
            reason: "Suspicious activity",
          });
          revoked = true;
          body = {
            agent_id: id,
            revoked: true,
            sessions_revoked: [session],
            rotated: [ref],
            flagged: [],
          };
        } else throw new Error("Unexpected endpoint");
        return new Response(JSON.stringify(body), {
          headers: { "x-burn-mock": "true" },
        });
      });
    vi.stubGlobal("fetch", fetcher);
    await act(async () => {
      render(
        <App
          config={{
            burnUrl: "https://burn.example.test",
            demoToken: crypto.randomUUID(),
            leakTestUrl: "https://test.example.test",
          }}
        />,
      );
    });
    fireEvent.click(screen.getByRole("button", { name: `Burn ${id}` }));
    const dialog = screen.getByRole("dialog");
    expect(document.activeElement).toBe(
      within(dialog).getByRole("button", { name: "Cancel" }),
    );
    expect(
      fetcher.mock.calls.some((call) => String(call[0]).endsWith("/burn")),
    ).toBe(false);
    fireEvent.change(within(dialog).getByLabelText(/Reason/), {
      target: { value: "Suspicious activity" },
    });
    await act(async () => {
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Confirm Burn" }),
      );
    });
    const outcome = screen.getByRole("status", { name: "Burn result" });
    expect(outcome.textContent).toContain(ref);
    expect(outcome.textContent).toContain("1 sessions revoked");
    expect(screen.getByText("MOCK DATA")).toBeTruthy();
    expect(
      fetcher.mock.calls.some((call) =>
        String(call[0]).endsWith("/blast-radius"),
      ),
    ).toBe(true);
    expect(screen.getByText(/CloudTrail event history can lag/)).toBeTruthy();
    session = crypto.randomUUID();
    revoked = false;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(
      (screen.getByRole("button", { name: `Burn ${id}` }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
    expect(screen.queryByRole("status", { name: "Burn result" })).toBeNull();
  });

  it.each([
    { status: "active", reason: "Credential match stopped" },
    { status: "revoked", reason: "session_inactive" },
  ])("reports $status proxy outcomes", async ({ status, reason }) => {
    const id = `agent-${crypto.randomUUID()}`;
    const payload = crypto.randomUUID();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async (input, init) => {
        const path = new URL(String(input)).pathname;
        if (path === "/leak-test") {
          expect(JSON.parse(String(init?.body))).toEqual({
            agent_id: id,
            encoding: "plain",
            free_text: payload,
          });
          return new Response(
            JSON.stringify({
              blocked: true,
              encoding: "plain",
              reason,
            }),
          );
        }
        const body =
          path === "/events"
            ? { events: [], agents: [{ agent_id: id, status }] }
            : path === "/encodings"
              ? { encodings: ["plain"] }
              : path.endsWith("/blast-radius")
                ? { agent_id: id, keys: [], events: [] }
                : { agent_id: id, entries: [] };
        return new Response(JSON.stringify(body));
      });
    vi.stubGlobal("fetch", fetcher);
    await act(async () => {
      render(
        <App
          config={{
            burnUrl: "https://burn.example.test",
            demoToken: crypto.randomUUID(),
            leakTestUrl: "https://test.example.test",
          }}
        />,
      );
    });
    expect(
      (
        screen.getByRole("button", {
          name: "Send test ↗",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
    fireEvent.change(screen.getByLabelText(/Free-text payload/), {
      target: { value: payload },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send test ↗" }));
    });
    expect(screen.getByText("⊘ Blocked")).toBeTruthy();
    expect(screen.getByText(reason)).toBeTruthy();
    expect(
      (screen.getByLabelText(/Free-text payload/) as HTMLTextAreaElement).value,
    ).toBe("");
    expect(screen.queryByText(payload)).toBeNull();
  });

  it("renders nullable live metadata while the test tool is offline, then recovers automatically", async () => {
    const id = `agent-${crypto.randomUUID()}`;
    const ref = `burn/${id}/aws_key#v1`;
    let testAvailable = false;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const path = new URL(String(input)).pathname;
      let body: unknown;
      if (path === "/encodings") {
        if (!testAvailable) throw new TypeError("Service unavailable");
        body = { encodings: ["plain"] };
      } else if (path === "/events")
        body = {
          agents: [
            {
              agent_id: id,
              role: null,
              status: "revoked",
              session_id: null,
              exposures: 1,
            },
          ],
          events: [
            {
              event_id: `evt_${String(1).padStart(26, "0")}`,
              ts: new Date().toISOString(),
              agent_id: id,
              source: "session",
              action: "issued",
              session_id: null,
              secret_ref: null,
              fingerprint: null,
              secret_type: null,
              owner: null,
              owner_agent_id: null,
              target_agent_id: null,
              destination: null,
              encoding: null,
              detail: null,
              tool: null,
            },
          ],
          cursor: null,
        };
      else if (path.endsWith("/ledger"))
        body = {
          agent_id: id,
          entries: [
            {
              secret_ref: null,
              fingerprint: crypto.randomUUID().replaceAll("-", "").repeat(2),
              secret_type: "other",
              owner: "unknown",
              owner_agent_id: null,
              source: "tool_response",
              via: null,
              first_seen: null,
              status: "active",
            },
          ],
        };
      else if (path.endsWith("/blast-radius"))
        body = {
          agent_id: id,
          keys: [{ key_ref: ref, key_hint: null, status: "inactive" }],
          events: [
            {
              ts: new Date().toISOString(),
              key_ref: ref,
              event_name: "ListBuckets",
              event_source: null,
              resource: null,
              source_ip: null,
              error_code: null,
            },
          ],
        };
      else throw new Error("Unexpected endpoint");
      return new Response(JSON.stringify(body));
    });
    vi.stubGlobal("fetch", fetcher);
    await act(async () => {
      render(
        <App
          config={{
            burnUrl: "https://burn.example.test",
            demoToken: crypto.randomUUID(),
            leakTestUrl: "https://test.example.test",
          }}
        />,
      );
    });
    expect(screen.getByText("Session or credential issued")).toBeTruthy();
    expect(screen.getByText("Unidentified credential")).toBeTruthy();
    expect(screen.getByText("Source unavailable")).toBeTruthy();
    expect(screen.getByText("No active session")).toBeTruthy();
    expect(screen.getByText(/Leak-test service unavailable/)).toBeTruthy();
    expect(screen.getByText("Connected · polling every second")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(
      (screen.getByRole("button", { name: "Send test ↗" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.queryByText("MOCK DATA")).toBeNull();
    testAvailable = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(screen.queryByText(/Leak-test service unavailable/)).toBeNull();
    expect(
      (screen.getByRole("button", { name: "Send test ↗" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });
});
