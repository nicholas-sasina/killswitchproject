/**
 * The console's pages, addressed by hash so each is a real location: it
 * survives a reload, can be bookmarked, and can be opened in a second window.
 *
 *   #/overview               what is running now, and what was burned today
 *   #/roles                  every role
 *   #/roles/<name>           one role: its agents, permissions and tokens
 *   #/agents/<session_id>    one agent: its ledger, timeline and containment
 *   #/incidents              burned agents and what is still open
 *   #/credentials            flagged shared keys and their owners' decisions
 *
 * Old links keep working: #/burned opens Incidents, #/alerts the Overview,
 * and #/overview?instance=<id> the agent it named.
 *
 * Hash rather than history routing because the dashboard is served as static
 * files with no server-side rewrite, so a path like /roles would 404 on
 * reload. Deliberately not a routing library: six routes do not justify a
 * dependency.
 */
import { useEffect, useState } from "react";

export type Route =
  | { page: "overview" }
  | { page: "roles" }
  | { page: "role"; name: string }
  | { page: "agent"; sessionId: string }
  | { page: "incidents" }
  | { page: "credentials" };

export type Page = Route["page"];

/** The four places in the navigation. A role and an agent sit under Roles. */
export type Section = "overview" | "roles" | "incidents" | "credentials";

export function sectionOf(route: Route): Section {
  switch (route.page) {
    case "role":
    case "agent":
      return "roles";
    default:
      return route.page;
  }
}

function decode(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

/**
 * The route a hash names, and whether the hash should be rewritten to that
 * route's own address (an old link, or one with nothing after the #).
 */
export function parseHash(hash: string): { route: Route; canonical: boolean } {
  const [path, query = ""] = hash.replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean);
  const [head, rest] = [parts[0] ?? "", parts.slice(1).join("/")];
  let route: Route;
  switch (head) {
    case "roles":
      route = rest ? { page: "role", name: decode(rest) } : { page: "roles" };
      break;
    case "agents":
      route = rest
        ? { page: "agent", sessionId: decode(rest) }
        : { page: "roles" };
      break;
    case "incidents":
    case "burned":
      route = { page: "incidents" };
      break;
    case "credentials":
      route = { page: "credentials" };
      break;
    default: {
      // #/overview, #/alerts, an empty hash and anything unknown. The old
      // overview opened an instance with ?instance=<session_id>.
      const instance = new URLSearchParams(query).get("instance");
      route = instance
        ? { page: "agent", sessionId: instance }
        : { page: "overview" };
    }
  }
  return { route, canonical: hash === routeHref(route) };
}

export function routeFromHash(hash: string): Route {
  return parseHash(hash).route;
}

export function routeHref(route: Route): string {
  switch (route.page) {
    case "role":
      return roleHref(route.name);
    case "agent":
      return agentHref(route.sessionId);
    default:
      return `#/${route.page}`;
  }
}

export function roleHref(name: string): string {
  return `#/roles/${encodeURIComponent(name)}`;
}

export function agentHref(sessionId: string): string {
  return `#/agents/${encodeURIComponent(sessionId)}`;
}

/** An agent's page, by the session the API calls an instance. */
export const instanceHref = agentHref;

/**
 * The current route. An old or empty hash is rewritten in place to the
 * route's own address, without adding a history entry.
 */
export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() =>
    routeFromHash(typeof window === "undefined" ? "" : window.location.hash),
  );
  useEffect(() => {
    const onChange = () => {
      const { route: next, canonical } = parseHash(window.location.hash);
      if (!canonical)
        window.history.replaceState(
          window.history.state,
          "",
          `${window.location.pathname}${window.location.search}${routeHref(next)}`,
        );
      setRoute((current) => (sameRoute(current, next) ? current : next));
    };
    window.addEventListener("hashchange", onChange);
    // The hash may have changed between first render and this effect.
    onChange();
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function sameRoute(a: Route, b: Route): boolean {
  return routeHref(a) === routeHref(b);
}
