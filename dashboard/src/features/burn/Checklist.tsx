// STUB: the shared interface only. The agent page's branch implements the
// ticking (one step every ~450 ms, final state at once with reduced motion).
import type { ContainmentStep } from "./types";

export interface ContainmentChecklistProps {
  steps: ContainmentStep[];
  /** How many steps are done: before a burn 0; after, all of them. */
  done: number;
  /** Tick from the current `done` up to `steps.length`, ~450 ms apart. */
  animate?: boolean;
}

export function ContainmentChecklist({
  steps,
  done,
}: ContainmentChecklistProps) {
  return (
    <ol className="checklist">
      {steps.map((step, index) => (
        <li key={step.id} data-done={index < done || undefined}>
          {step.title}
          {step.sub && <span>{step.sub}</span>}
        </li>
      ))}
    </ol>
  );
}

/**
 * The steps a burn took, in order: revoked, each owned key rotated, the
 * shared keys flagged, everyone else untouched. STUB: returns no steps.
 */
export function containmentSteps(): ContainmentStep[] {
  return [];
}
