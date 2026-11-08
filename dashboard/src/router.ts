/**
 * Two views, addressed by hash so each is a real location: it survives a
 * reload, can be bookmarked, and can be put on a second screen during a demo.
 *
 * Hash rather than history routing because the dashboard is served as static
 * files by Vite with no server-side rewrite, so a path like /alerts would
 * 404 on reload.
 *
 * Deliberately not a routing library. Two routes do not justify a dependency.
 */
import { useEffect, useState } from "react";

export const VIEWS = ["overview", "alerts"] as const;
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
