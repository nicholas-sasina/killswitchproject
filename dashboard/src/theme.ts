/**
 * Theme follows the operating system. There is no in-app switch.
 *
 * The operator's machine already knows whether they want light or dark, and
 * it tracks sunset, Night Shift and corporate policy without being asked.
 * A second control that can disagree with the OS is one more thing to get
 * out of sync, and it spends header space on a decision nobody needs to
 * revisit.
 *
 * The attribute lives on <html> and is also set by public/first-paint.js,
 * which index.html runs before the first paint, so that paint is already
 * correct. Without that the page flashes light before React mounts.
 */
import { useEffect, useState } from "react";

export type Resolved = "light" | "dark";

const QUERY = "(prefers-color-scheme: dark)";

function canMatch(): boolean {
  return (
    typeof window !== "undefined" && typeof window.matchMedia === "function"
  );
}

/** jsdom and older browsers have no matchMedia; those default to light. */
export function systemTheme(): Resolved {
  return canMatch() && window.matchMedia(QUERY).matches ? "dark" : "light";
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

/** Applies the system theme and follows it when the system changes. */
export function useSystemTheme(): Resolved {
  const [resolved, setResolved] = useState<Resolved>(systemTheme);
  useEffect(() => {
    apply(resolved);
  }, [resolved]);
  useEffect(() => {
    if (!canMatch()) return;
    const query = window.matchMedia(QUERY);
    const onChange = () => setResolved(query.matches ? "dark" : "light");
    query.addEventListener("change", onChange);
    // The setting may have changed between first render and this effect.
    onChange();
    return () => query.removeEventListener("change", onChange);
  }, []);
  return resolved;
}
