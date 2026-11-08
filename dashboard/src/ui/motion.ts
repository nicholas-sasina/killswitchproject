/**
 * The one reduced-motion helper. With prefers-reduced-motion nothing moves:
 * no dimming, no rolling, no ticking, and dialogs appear at once. Components
 * that animate from script ask here; CSS animations sit inside
 * `@media (prefers-reduced-motion: no-preference)`.
 */
import { useSyncExternalStore } from "react";

export const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

function matches(query: string): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia(query).matches
  );
}

export function prefersReducedMotion(): boolean {
  return matches(REDUCED_MOTION);
}

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      if (typeof window.matchMedia !== "function") return () => {};
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    () => matches(query),
    () => false,
  );
}

export function useReducedMotion(): boolean {
  return useMediaQuery(REDUCED_MOTION);
}
