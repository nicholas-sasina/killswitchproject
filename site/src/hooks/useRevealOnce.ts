import { useLayoutEffect, useState, type RefObject } from "react";
import { prefersReducedMotion } from "./media";

export type RevealPhase = "static" | "armed" | "play";

/**
 * For animations that play once, the first time something is scrolled into
 * view. Starts "static" (fully shown), so nothing is hidden without script or
 * with reduced motion; "armed" hides it until it is half on screen.
 */
export function useRevealOnce(ref: RefObject<Element | null>): RevealPhase {
  const [phase, setPhase] = useState<RevealPhase>("static");

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || prefersReducedMotion()) return;
    if (typeof IntersectionObserver === "undefined") return;
    setPhase("armed");
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && entry.intersectionRatio >= 0.5) {
          setPhase("play");
          io.disconnect();
        }
      },
      { threshold: [0, 0.5, 1] },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [ref]);

  return phase;
}
