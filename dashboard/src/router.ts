/**
 * Three views, addressed by hash so each is a real location: it survives a
 * reload, can be bookmarked, and can be put on a second screen during a demo.
 *
 *   overview  the live fleet: active agents, their roles and instances
 *   burned    contained agents, their history, and how to rebuild them
 *   alerts    only what needs an operator, worst first
 *
 * Splitting them keeps any one page from becoming a long stack of equally
 * weighted panels, and means an unmounted view stops polling.
 *
 * Hash rather than history routing because the dashboard is served as static
 * files by Vite with no server-side rewrite, so a path like /alerts would
 * 404 on reload.
 *
 * Deliberately not a routing library. Three routes do not justify a
 * dependency.
 */
import { useEffect, useState } from "react";

export const VIEWS = ["overview", "burned", "alerts"] as const;
export type View = (typeof VIEWS)[number];

export function viewFromHash(hash: string): View {
  const name = hash.replace(/^#\/?/, "").split(/[?/]/)[0];
  return (VIEWS as readonly string[]).includes(name)
    ? (name as View)
    : "overview";
}

export function hrefFor(view: View): string {
  return `#/${view}`;
}

export function useView(): View {
  const [view, setView] = useState<View>(() =>
    viewFromHash(typeof window === "undefined" ? "" : window.location.hash),
  );
  useEffect(() => {
    const onChange = () => setView(viewFromHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    // The hash may have changed between first render and this effect.
    onChange();
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return view;
}
