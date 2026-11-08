import { describe, expect, it } from "vitest";
import {
  COMPROMISED_CELL,
  HERO_CYCLE_MS,
  HERO_EVENTS,
  HERO_STEP_COUNT,
  heroStep,
  heroStepAt,
} from "./scenario";
import {
  PIPELINE_CYCLE_MS,
  PIPELINE_EVENTS,
  PIPELINE_PACE,
  PIPELINE_STARTS,
  pipelineState,
  pipelineStepAt,
  pipelineStepDone,
} from "./pipeline";

describe("heroStep", () => {
  it("has six steps on a 3.2 s beat", () => {
    expect(HERO_STEP_COUNT).toBe(6);
    expect(HERO_EVENTS).toEqual([0, 3200, 6400, 9600, 12800, 16000]);
    expect(HERO_CYCLE_MS).toBe(19200);
    expect(heroStepAt(0)).toBe(0);
    expect(heroStepAt(3199)).toBe(0);
    expect(heroStepAt(3200)).toBe(1);
    expect(heroStepAt(19199)).toBe(5);
  });

  it("starts with twenty running and nothing flagged", () => {
    const s = heroStep(0);
    expect(s.number).toBe("01");
    expect(s.status).toEqual({ text: "All systems nominal", tone: "neutral" });
    expect([s.running, s.contained]).toEqual([20, 0]);
    expect(s.rows.map((r) => r.action.text)).toEqual([
      "Monitoring",
      "Monitoring",
      "Monitoring",
      "Monitoring",
    ]);
    expect(s.cells.every((c) => c === "idle")).toBe(true);
    expect(s.ticket.untrusted).toBe(false);
  });

  it("marks the ticket untrusted from the exposure step on", () => {
    expect(heroStep(1).ticket.untrusted).toBe(true);
    expect(heroStep(5).ticket.untrusted).toBe(true);
  });

  it("walks worker-07's cell from watched to burning to burned", () => {
    const cell = (k: number) => heroStep(k).cells[COMPROMISED_CELL];
    expect([0, 1, 2, 3, 4, 5].map(cell)).toEqual([
      "idle",
      "watched",
      "watched",
      "burning",
      "burned",
      "burned",
    ]);
    // Nobody else is ever touched.
    for (let k = 0; k < 6; k++) {
      const others = heroStep(k).cells.filter((_, i) => i !== COMPROMISED_CELL);
      expect(others.every((c) => c === "idle")).toBe(true);
    }
  });

  it("blocks the decoy on the way out without burning anyone", () => {
    const s = heroStep(2);
    expect(s.status).toEqual({ text: "1 leak blocked", tone: "flag" });
    expect(s.rows[0].action).toEqual({ text: "Blocked", tone: "flag" });
    expect(s.cells[COMPROMISED_CELL]).toBe("watched");
    expect([s.running, s.contained]).toEqual([20, 0]);
  });

  it("burns on the canary and contains one worker", () => {
    expect(heroStep(3).status.tone).toBe("burn");
    expect(heroStep(3).rows[0].action).toEqual({ text: "Burn", tone: "burn" });
    const s = heroStep(4);
    expect(s.status.text).toBe("Contained");
    expect([s.running, s.contained]).toEqual([19, 1]);
    expect(s.rows.map((r) => r.action.text)).toEqual([
      "Burned",
      "Tracked",
      "Rotated",
      "Rotated",
    ]);
    expect(s.control.count).toBe("19 running, 1 burned");
  });

  it("never exposes the shared key's value, so it is kept, not flagged", () => {
    for (let k = 1; k < 6; k++) {
      const shared = heroStep(k).rows[1];
      expect(shared.evidence).not.toMatch(/plain text/i);
      expect(shared.action.text).not.toMatch(/flag/i);
    }
    const s = heroStep(5);
    expect(s.status).toEqual({ text: "No review needed", tone: "done" });
    expect(s.rows[1].action).toEqual({ text: "Kept", tone: "done" });
  });

  it("clamps out-of-range steps", () => {
    expect(heroStep(-3).index).toBe(0);
    expect(heroStep(99).index).toBe(5);
  });
});

describe("pipelineState", () => {
  // The script's times, written for a 17 s loop, at the pace the page plays them.
  const at = (time: number) => time * PIPELINE_PACE;

  it("runs six steps over 23.8 s", () => {
    expect(PIPELINE_STARTS).toEqual([0, 4200, 7700, 11200, 14700, 18200]);
    expect(PIPELINE_CYCLE_MS).toBe(23800);
    expect(pipelineStepAt(at(3000) - 1)).toBe(0);
    expect(pipelineStepAt(at(3000))).toBe(1);
    expect(pipelineStepAt(at(17000) - 1)).toBe(5);
    expect(pipelineStepAt(at(17000))).toBe(0);
  });

  it("lists every change as an event, sorted and unique", () => {
    expect(PIPELINE_EVENTS[0]).toBe(0);
    expect([...PIPELINE_EVENTS].sort((a, b) => a - b)).toEqual(PIPELINE_EVENTS);
    expect(new Set(PIPELINE_EVENTS).size).toBe(PIPELINE_EVENTS.length);
    // Nothing changes between two events.
    for (let i = 0; i < PIPELINE_EVENTS.length; i++) {
      const from = PIPELINE_EVENTS[i];
      const to = PIPELINE_EVENTS[i + 1] ?? PIPELINE_CYCLE_MS;
      const { t: _a, ...first } = pipelineState(from);
      const { t: _b, ...last } = pipelineState(to - 1);
      expect(last).toEqual(first);
    }
  });

  it("records the ledger one mark at a time at baseline", () => {
    expect(pipelineState(at(0)).ledger.marks).toEqual([
      false,
      false,
      false,
      false,
    ]);
    expect(pipelineState(at(1000)).ledger.marks).toEqual([
      true,
      true,
      false,
      false,
    ]);
    expect(pipelineState(at(3000) - 1).ledger).toEqual({
      marks: [true, true, true, true],
      done: true,
    });
    expect(pipelineState(at(0)).panels.ledger).toBe("active");
  });

  it("lights the exit chips in order", () => {
    expect(pipelineState(at(3000)).exit.chips).toEqual([false, false, false]);
    expect(pipelineState(at(4000)).exit.chips).toEqual([true, true, false]);
    expect(pipelineState(at(5000)).exit.done).toBe(true);
  });

  it("trips the canary, then identifies worker-07", () => {
    const tripped = pipelineState(at(5500));
    expect(tripped.panels.canary).toBe("hot");
    expect(tripped.canary.identified).toBe(false);
    expect(tripped.support).toEqual({ hot: true, status: "identified" });
    expect(pipelineState(at(6000)).canary.identified).toBe(true);
    expect(pipelineState(at(8000)).support.status).toBe("burned");
  });

  it("uses ember only for the canary-to-decision connector", () => {
    const end = pipelineState(at(17000) - 1).lines;
    expect(end).toEqual({
      exit: "ink",
      canary: "ink",
      decision: "ember",
      human: "ink",
      result: "ink",
    });
    expect(pipelineState(at(0)).lines.exit).toBe("idle");
  });

  it("ticks the result rows off one by one", () => {
    expect(pipelineState(at(13000)).result).toEqual([
      true,
      false,
      false,
      false,
    ]);
    expect(pipelineState(at(14500)).result).toEqual([true, true, true, true]);
  });

  it("shows a step's finished state for the step buttons", () => {
    expect(pipelineStepDone(0)).toBe(at(3000) - 1);
    expect(pipelineStepDone(5)).toBe(at(17000) - 1);
    expect(pipelineState(pipelineStepDone(3)).decision).toEqual({
      burn: true,
      credential: true,
      done: true,
    });
  });
});
