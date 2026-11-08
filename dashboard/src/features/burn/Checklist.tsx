/**
 * The containment checklist: what a burn will do, greyed, before it; what
 * it did, ticked, after. When a burn's answer arrives the steps tick in
 * order, one about every 450 ms; with reduced motion they show done at once.
 * Only this list re-renders while it ticks.
 */
import { useEffect, useState } from "react";
import { useReducedMotion } from "../../ui";
import type { ContainmentStep } from "./types";
import "./burn.css";

export { containmentSteps, type StepContext } from "./steps";

/** Time between two ticks. */
export const TICK_MS = 450;

export interface ContainmentChecklistProps {
  steps: ContainmentStep[];
  /** How many steps are done: before a burn 0; after, all of them. */
  done: number;
  /** Tick from the current `done` up to `steps.length`, ~450 ms apart. */
  animate?: boolean;
}

function Mark({ state }: { state: "todo" | "done" | "error" }) {
  return (
    <svg
      className="checklist-mark"
      width="14"
      height="14"
      viewBox="0 0 14 14"
      aria-hidden="true"
    >
      {state === "error" ? (
        <path d="M7 2.75v5.5M7 10.75v.5" />
      ) : (
        <path d="M2.5 7.5 5.5 10.5 11.5 3.5" />
      )}
    </svg>
  );
}

const STATE_WORDS = {
  todo: "Not done: ",
  done: "Done: ",
  error: "Didn't finish: ",
} as const;

export function ContainmentChecklist({
  steps,
  done,
  animate = false,
}: ContainmentChecklistProps) {
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(done);
  const ticking = animate && !reduced;
  const count = Math.min(
    steps.length,
    !animate ? done : reduced ? steps.length : shown,
  );

  useEffect(() => {
    if (!ticking || shown >= steps.length) return;
    const timer = window.setTimeout(
      () => setShown((current) => current + 1),
      TICK_MS,
    );
    return () => window.clearTimeout(timer);
  }, [ticking, shown, steps.length]);

  const last = count > 0 ? steps[count - 1] : undefined;
  return (
    <div className="checklist-wrap">
      <ol className={`checklist${ticking ? " is-ticking" : ""}`}>
        {steps.map((step, index) => {
          const state =
            index < count ? (step.error ? "error" : "done") : "todo";
          return (
            <li
              key={step.id}
              className={`checklist-item is-${state}${step.hot ? " is-hot" : ""}`}
            >
              <Mark state={state} />
              <span className="checklist-text">
                <span className="checklist-title">
                  <span className="visually-hidden">{STATE_WORDS[state]}</span>
                  {step.title}
                </span>
                {step.sub && <span className="checklist-sub">{step.sub}</span>}
                {state === "error" && (
                  <span className="checklist-error">{step.error}</span>
                )}
              </span>
            </li>
          );
        })}
      </ol>
      {animate && (
        <p className="visually-hidden" aria-live="polite">
          {last
            ? `${last.title}${last.error ? `: ${last.error.replace(/\.$/, "")}` : ""}. ${count} of ${steps.length} done.`
            : ""}
        </p>
      )}
    </div>
  );
}
