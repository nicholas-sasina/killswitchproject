// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";
import { ApiError } from "../../api";
import type { Flag, FlagResolution, FlagResolveResult } from "../../contracts";
import type { ConsoleData } from "../../data/ConsoleData";
import { FlagActions } from "./FlagActions";
import { consoleValue, flag, WithConsole } from "./testConsole";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

type Resolve = Mock<(body: FlagResolution) => Promise<FlagResolveResult>>;

function setup({
  resolve = vi.fn<Resolve>().mockResolvedValue({}),
  email,
  variant,
  state = "open",
  onResolved = vi.fn<(flag: Flag, result: FlagResolveResult) => void>(),
}: {
  resolve?: Resolve;
  email?: string;
  variant?: "compact" | "full";
  state?: "open" | "dismissed";
  onResolved?: Mock<(flag: Flag, result: FlagResolveResult) => void>;
} = {}) {
  const value: ConsoleData = consoleValue({
    api: { resolveFlag: resolve },
    email,
  });
  const target = flag({ state });
  render(
    <WithConsole value={value}>
      <section aria-labelledby="still-open">
        <h2 id="still-open">Still open</h2>
        <FlagActions flag={target} variant={variant} onResolved={onResolved} />
      </section>
    </WithConsole>,
  );
  return { value, resolve, onResolved, target };
}

function reasonField() {
  return screen.getByRole("textbox", { name: "Reason" });
}

async function decide(button: RegExp, reason: string, submit: RegExp) {
  fireEvent.click(screen.getByRole("button", { name: button }));
  fireEvent.change(reasonField(), { target: { value: reason } });
  await act(async () => {
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: submit }),
    );
  });
}

describe("FlagActions", () => {
  it("names the key on each button, and says Dismiss in the compact form", () => {
    setup();
    expect(
      screen.getByRole("button", { name: "Rotate crm_db_url" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", {
        name: "Dismiss with a reason for crm_db_url",
      }),
    ).toBeTruthy();
    cleanup();
    setup({ variant: "compact" });
    expect(
      screen.getByRole("button", { name: "Dismiss for crm_db_url" }),
    ).toBeTruthy();
  });

  it("does nothing for a flag that is no longer open", () => {
    setup({ state: "dismissed" });
    expect(
      (screen.getByRole("button", { name: /^Rotate/ }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("asks for a reason before rotating, and sends nothing without one", async () => {
    const { resolve } = setup();
    fireEvent.click(screen.getByRole("button", { name: /^Rotate/ }));
    const dialog = screen.getByRole("dialog", { name: "Rotate crm_db_url?" });
    expect(document.activeElement).toBe(reasonField());
    expect(dialog.textContent).toContain(
      "Anything outside Burn that still holds the old value stops working",
    );
    await act(async () => {
      fireEvent.click(
        within(dialog).getByRole("button", { name: "Rotate crm_db_url" }),
      );
    });
    expect(resolve).not.toHaveBeenCalled();
    expect(reasonField().getAttribute("aria-invalid")).toBe("true");
    expect(dialog.textContent).toContain("at least 3 characters");
  });

  it("rotates with the reason and the signed-in person, then updates the list at once", async () => {
    vi.useFakeTimers();
    const rotated = flag({ state: "rotated" });
    const resolve = vi.fn<Resolve>().mockResolvedValue({
      flag: rotated,
      rotated_to_version: 3,
    });
    const { value, onResolved } = setup({
      resolve,
      email: "priya.nair@example.com",
    });
    await decide(/^Rotate/, "  owner rotated after review  ", /^Rotate crm/);
    expect(resolve).toHaveBeenCalledWith({
      secret_ref: "burn/shared/crm_db_url",
      action: "rotate",
      reason: "owner rotated after review",
      by: "priya.nair@example.com",
    });
    expect(value.noteFlag).toHaveBeenCalledWith(rotated);
    expect(value.refreshFlags).toHaveBeenCalled();
    expect(onResolved).toHaveBeenCalledWith(rotated, {
      flag: rotated,
      rotated_to_version: 3,
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(
      document.body.querySelector('[role="status"][aria-live="polite"]')
        ?.textContent,
    ).toBe("crm_db_url rotated. Agents read the new value through references.");
  });

  it("dismisses without a `by` the API would refuse, and notes the decision itself when the API returns no flag", async () => {
    const { value, resolve } = setup({ email: "o'brien+burn@example.com" });
    await decide(/^Dismiss with/, "test fixture, not real", /^Dismiss the/);
    expect(resolve).toHaveBeenCalledWith({
      secret_ref: "burn/shared/crm_db_url",
      action: "dismiss",
      reason: "test fixture, not real",
    });
    expect(value.noteFlag).toHaveBeenCalledWith(
      expect.objectContaining({
        state: "dismissed",
        resolution: expect.objectContaining({
          action: "dismiss",
          reason: "test fixture, not real",
          level: "seen",
        }),
      }),
    );
  });

  it("says plainly why Burn refused, and keeps the dialog open", async () => {
    const resolve = vi
      .fn<Resolve>()
      .mockRejectedValue(new ApiError("API request failed (HTTP 400).", 400));
    const { value } = setup({ resolve });
    await decide(/^Rotate/, "contains AKIA-something", /^Rotate crm/);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("alert").textContent).toBe(
      "Burn refused this reason. Write 3 to 300 characters, and never include a key's value.",
    );
    expect(value.noteFlag).not.toHaveBeenCalled();
    // The button works again for a second try.
    expect(
      within(dialog)
        .getByRole("button", { name: "Rotate crm_db_url" })
        .getAttribute("aria-disabled"),
    ).toBeNull();
  });

  it("refreshes the list when the key is no longer flagged", async () => {
    const resolve = vi
      .fn<Resolve>()
      .mockRejectedValue(new ApiError("API request failed (HTTP 404).", 404));
    const { value } = setup({ resolve });
    await decide(/^Dismiss with/, "already handled", /^Dismiss the/);
    expect(screen.getByRole("alert").textContent).toBe(
      "crm_db_url is no longer flagged. Someone may have resolved it already.",
    );
    expect(value.refreshFlags).toHaveBeenCalled();
  });

  it("sends one request however often it is pressed", async () => {
    let finish: (value: FlagResolveResult) => void = () => {};
    const resolve = vi.fn<Resolve>().mockReturnValue(
      new Promise<FlagResolveResult>((done) => {
        finish = done;
      }),
    );
    setup({ resolve });
    fireEvent.click(screen.getByRole("button", { name: /^Rotate/ }));
    fireEvent.change(reasonField(), { target: { value: "rotate it now" } });
    const submit = within(screen.getByRole("dialog")).getByRole("button", {
      name: "Rotate crm_db_url",
    });
    await act(async () => {
      fireEvent.click(submit);
      fireEvent.click(submit);
    });
    expect(resolve).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Rotating…" })).toBeTruthy();
    // Escape does nothing while the request runs.
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeTruthy();
    await act(async () => finish({}));
  });

  it("closes on Cancel and gives focus back to the button that opened it", () => {
    setup();
    const opener = screen.getByRole("button", { name: /^Dismiss with/ });
    opener.focus();
    fireEvent.click(opener);
    expect(
      screen.getByRole("dialog", { name: "Dismiss the flag on crm_db_url?" })
        .textContent,
    ).toContain(
      "It reopens only if evidence at attempted or reported arrives.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("keeps focus in the card when the decided row disappears", async () => {
    const value = consoleValue({
      api: { resolveFlag: vi.fn<Resolve>().mockResolvedValue({}) },
    });
    function StillOpen() {
      const [gone, setGone] = useState(false);
      return (
        <section aria-labelledby="open-title">
          <h2 id="open-title">Still open</h2>
          {!gone && (
            <FlagActions flag={flag()} onResolved={() => setGone(true)} />
          )}
        </section>
      );
    }
    render(
      <WithConsole value={value}>
        <StillOpen />
      </WithConsole>,
    );
    await decide(/^Dismiss with/, "fixture key, not real", /^Dismiss the/);
    expect(screen.queryByRole("button", { name: /^Rotate/ })).toBeNull();
    await act(async () => {
      await new Promise((done) => setTimeout(done, 50));
    });
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { name: "Still open" }),
    );
  });
});
