// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createPulseBus } from "../model/activity";
import { Dialog } from "./Dialog";
import { DigitRoll } from "./DigitRoll";
import { Facts } from "./bits";
import { Hero } from "./Hero";
import { PulseContext } from "./pulse";
import { Segmented } from "./Segmented";
import { Square } from "./Square";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function Opener() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Burn worker-07
      </button>
      {open && (
        <Dialog labelledBy="t" onClose={() => setOpen(false)}>
          <h2 id="t">Burn worker-07?</h2>
          <button type="button">Confirm</button>
          <button type="button" onClick={() => setOpen(false)}>
            Cancel
          </button>
        </Dialog>
      )}
    </>
  );
}

describe("Dialog", () => {
  it("traps focus, closes on Escape and returns focus to its opener", () => {
    render(<Opener />);
    const opener = screen.getByRole("button", { name: "Burn worker-07" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = screen.getByRole("dialog", { name: "Burn worker-07?" });
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    const confirm = screen.getByRole("button", { name: "Confirm" });
    const cancel = screen.getByRole("button", { name: "Cancel" });
    expect(document.activeElement).toBe(confirm);
    cancel.focus();
    fireEvent.keyDown(cancel, { key: "Tab" });
    expect(document.activeElement).toBe(confirm);
    fireEvent.keyDown(confirm, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(cancel);
    fireEvent.keyDown(cancel, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it("stays open on Escape while busy", () => {
    const onClose = vi.fn();
    render(
      <Dialog labelledBy="t" onClose={onClose} busy>
        <h2 id="t">Burning</h2>
      </Dialog>,
    );
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("Square", () => {
  it("states the agent and its state in its label and tooltip", () => {
    render(
      <Square
        state="burned"
        label="worker-07, burned at 7:42 PM"
        href="#/agents/s"
      />,
    );
    const link = screen.getByRole("link", {
      name: "worker-07, burned at 7:42 PM",
    });
    expect(link.getAttribute("title")).toBe("worker-07, burned at 7:42 PM");
    expect(link.className).toContain("square-burned");
  });

  it("dims only when its agent made a call, and never with reduced motion", () => {
    const bus = createPulseBus();
    const animate = vi.fn();
    HTMLElement.prototype.animate = animate;
    const reduce = vi.fn(() => ({
      matches: false,
      addEventListener() {},
      removeEventListener() {},
    }));
    vi.stubGlobal("matchMedia", reduce);
    render(
      <PulseContext value={bus}>
        <Square state="running" label="worker-01, running" pulseKey="a" />
        <Square state="running" label="worker-02, running" pulseKey="b" />
      </PulseContext>,
    );
    act(() => bus.emit(["a"]));
    expect(animate).toHaveBeenCalledTimes(1);
    reduce.mockReturnValue({
      matches: true,
      addEventListener() {},
      removeEventListener() {},
    });
    act(() => bus.emit(["a", "b"]));
    expect(animate).toHaveBeenCalledTimes(1);
  });
});

describe("DigitRoll", () => {
  it("reads as the number, grouped, without zero padding", () => {
    const { container } = render(<DigitRoll value={41928} />);
    expect(container.querySelector(".visually-hidden")?.textContent).toBe(
      "41,928",
    );
    render(<DigitRoll value={19} />);
    expect(screen.getByText("19")).toBeTruthy();
  });
});

describe("Hero, Segmented and Facts", () => {
  it("gives the page its h1 and its figures", () => {
    render(
      <Hero
        title={["worker-07 was burned.", "The other 19 kept working."]}
        stats={[
          { label: "Running", value: 19 },
          { label: "Burned today", value: 1, tone: "ember" },
        ]}
      />,
    );
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
      "worker-07 was burned.The other 19 kept working.",
    );
    expect(screen.getByText("Burned today")).toBeTruthy();
  });

  it("presses one filter at a time", () => {
    const onChange = vi.fn();
    render(
      <Segmented
        label="Show"
        value="running"
        onChange={onChange}
        options={[
          { value: "running", label: "Running", count: 13 },
          { value: "burned", label: "Burned", count: 1 },
        ]}
      />,
    );
    expect(
      screen
        .getByRole("button", { name: "Running 13" })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Burned 1" }));
    expect(onChange).toHaveBeenCalledWith("burned");
  });

  it("leaves out a fact with no value", () => {
    render(
      <Facts
        rows={[
          { label: "Role", value: "worker" },
          { label: "Join token", value: null },
        ]}
      />,
    );
    expect(screen.queryByText("Join token")).toBeNull();
  });
});
