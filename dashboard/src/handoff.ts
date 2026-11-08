import { flushSync } from "react-dom";

/**
 * The swap from the sign-in screen to the dashboard. Where the browser has
 * view transitions and motion is allowed, the sign-in card shrinks into the
 * dashboard's brand while the dashboard fades in (see signin.css). Everywhere
 * else the swap is instant.
 */

/** The class that names the dashboard's brand as the card's destination. */
export const HANDOFF_CLASS = "signin-handoff";

export function canHandOff(): boolean {
  if (
    typeof document === "undefined" ||
    typeof document.startViewTransition !== "function"
  )
    return false;
  return !(
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** Runs `update`, the state change that shows the dashboard, inside a view transition when one can run. */
export function handOff(update: () => void): void {
  if (!canHandOff()) {
    update();
    return;
  }
  const root = document.documentElement;
  root.classList.add(HANDOFF_CLASS);
  const transition = document.startViewTransition(() => flushSync(update));
  void transition.finished.finally(() => root.classList.remove(HANDOFF_CLASS));
}
