import { useEffect, useRef, type RefObject } from "react";

type Options = {
  /** Only the current step's bar animates. */
  active: boolean;
  start: number;
  end: number;
  running: boolean;
  position: () => number;
  /** Changes whenever the bar must restart (a new step, a seek). */
  restartKey: unknown;
};

/**
 * Fills an element along X across a step, with WAAPI on transform only. The
 * animation is the visible half of the timeline: it pauses and resumes with it.
 */
export function useProgress(
  ref: RefObject<HTMLElement | null>,
  { active, start, end, running, position, restartKey }: Options,
) {
  const anim = useRef<Animation | null>(null);
  const span = end - start;
  const offset = () => Math.min(span, Math.max(0, position() - start));

  useEffect(() => {
    const el = ref.current;
    if (!el || !active || typeof el.animate !== "function") return;
    const a = el.animate(
      [{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }],
      { duration: span, fill: "both", easing: "linear" },
    );
    a.pause();
    a.currentTime = offset();
    anim.current = a;
    return () => {
      a.cancel();
      anim.current = null;
    };
    // offset() is read once, when the step starts; it is not a dependency.
  }, [ref, active, span, restartKey]);

  useEffect(() => {
    const a = anim.current;
    if (!a) return;
    a.currentTime = offset();
    if (running) a.play();
    else a.pause();
  }, [running, restartKey, active]);
}
