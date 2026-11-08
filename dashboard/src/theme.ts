/**
 * Theme choice: paper or ink.
 *
 * Three states, not two. "System" follows the operator's OS setting and is
 * the default, so a dark-mode machine gets ink without being asked. Choosing
 * paper or ink explicitly pins it and persists, because a demo room's
 * lighting rarely matches whatever the laptop decided.
 *
 * The attribute lives on <html> and is also set by a small inline script in
 * index.html, so the first paint is already the right theme. Without that the
 * page flashes paper before React mounts.
 */
import { useCallback, useEffect, useState } from "react";

export const THEME_KEY = "burn.theme";

export type ThemeChoice = "system" | "light" | "dark";
export type Resolved = "light" | "dark";

export const THEME_ORDER: ThemeChoice[] = ["system", "light", "dark"];

export const THEME_LABEL: Record<ThemeChoice, string> = {
  system: "System",
  light: "Paper",
  dark: "Ink",
};

function prefersDark(): boolean {
  // jsdom and older browsers have no matchMedia; default to paper there.
  return typeof window !== "undefined" &&
    typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)").matches
    : false;
}

export function readStored(): ThemeChoice {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" || value === "system"
      ? value
      : "system";
  } catch {
    // Private mode or blocked storage: fall back to following the system.
    return "system";
  }
}

export function resolve(choice: ThemeChoice): Resolved {
  return choice === "system" ? (prefersDark() ? "dark" : "light") : choice;
}

function apply(resolved: Resolved) {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = resolved;
  // Keep the browser's own UI (form controls, scrollbars) in step.
  document.documentElement.style.colorScheme = resolved;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", resolved === "dark" ? "#0d0b0a" : "#f1ede4");
}

export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(readStored);
  const resolved = resolve(choice);

  useEffect(() => {
    apply(resolved);
  }, [resolved]);

  // While following the system, track changes to it live.
  useEffect(() => {
    if (choice !== "system") return;
    if (typeof window === "undefined" || !window.matchMedia) return;
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => apply(resolve("system"));
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [choice]);

  const set = useCallback((next: ThemeChoice) => {
    setChoice(next);
    try {
      if (next === "system") localStorage.removeItem(THEME_KEY);
      else localStorage.setItem(THEME_KEY, next);
    } catch {
      // Not being able to persist is not a reason to refuse the change.
    }
  }, []);

  const cycle = useCallback(() => {
    set(THEME_ORDER[(THEME_ORDER.indexOf(choice) + 1) % THEME_ORDER.length]);
  }, [choice, set]);

  return { choice, resolved, set, cycle };
}
