import { memo, useContext, useEffect, useRef, type Ref } from "react";
import type { AgentState } from "../model/console";
import { prefersReducedMotion } from "./motion";
import { PulseContext } from "./pulse";
import "./ui.css";

/**
 * One agent as a square: ink while running, white with an ember edge once
 * burned, pale once finished. Its state is always in its label and tooltip,
 * never colour alone. A running square dims to 0.78 for 450 ms when the
 * event stream shows its agent made a call, animated on the element itself
 * so nothing re-renders; with reduced motion it stays still.
 */
export const Square = memo(function Square({
  state,
  label,
  href,
  pulseKey,
  size = 18,
  tabIndex,
  ref,
}: {
  state: AgentState;
  /** "worker-07, running": the accessible name and the tooltip. */
  label: string;
  href?: string;
  /** The agent's key, to dim on its calls. Leave out for a static square. */
  pulseKey?: string;
  size?: 18 | 14 | 12;
  /** For a roving tab stop across a row of squares. */
  tabIndex?: number;
  ref?: Ref<HTMLElement>;
}) {
  const bus = useContext(PulseContext);
  const own = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!bus || !pulseKey || state !== "running") return;
    return bus.subscribe(pulseKey, () => {
      const element = own.current;
      if (!element || prefersReducedMotion() || !element.animate) return;
      element.animate(
        [{ opacity: 1 }, { opacity: 0.78, offset: 0.3 }, { opacity: 1 }],
        { duration: 450, easing: "ease-out" },
      );
    });
  }, [bus, pulseKey, state]);
  const setRef = (element: HTMLElement | null) => {
    own.current = element;
    if (typeof ref === "function") ref(element);
    else if (ref) (ref as { current: HTMLElement | null }).current = element;
  };
  const className = `square square-${state} square-${size}`;
  return href ? (
    <a
      ref={setRef}
      className={className}
      href={href}
      aria-label={label}
      title={label}
      tabIndex={tabIndex}
    />
  ) : (
    <span
      ref={setRef}
      className={className}
      role="img"
      aria-label={label}
      title={label}
    />
  );
});
