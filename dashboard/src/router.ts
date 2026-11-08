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
  // A page may keep its own state in the query (#/roles/worker?show=burned);
  // only the old overview's ?instance= is rewritten away.
  const keep = head === "overview" || head === "" ? "" : query;
  return {
    route,
    canonical: hash === routeHref(route) + (keep ? `?${keep}` : ""),
  };
}

/** The query after the route: a page's own state, such as a filter. */
export function hashParams(hash: string): URLSearchParams {
  return new URLSearchParams(hash.split("?")[1] ?? "");
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
      if (!canonical) {
        const query = window.location.hash.split("?")[1];
        const keep =
          next.page !== "overview" && next.page !== "agent" && query
            ? `?${query}`
            : "";
        window.history.replaceState(
          window.history.state,
          "",
          `${window.location.pathname}${window.location.search}${routeHref(next)}${keep}`,
        );
      }
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

/**
 * One value in the hash query, kept in step with the address: a page's
 * filter that survives a reload and can be bookmarked. Setting it replaces
 * the history entry rather than adding one.
 */
export function useHashParam(
  name: string,
): [string | undefined, (value: string | undefined) => void] {
  const read = () =>
    typeof window === "undefined"
      ? undefined
      : (hashParams(window.location.hash).get(name) ?? undefined);
  const [value, setValue] = useState(read);
  useEffect(() => {
    const onChange = () => setValue(read());
    window.addEventListener("hashchange", onChange);
    onChange();
    return () => window.removeEventListener("hashchange", onChange);
    // read() depends only on name.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);
  const set = (next: string | undefined) => {
    const [path] = window.location.hash.split("?");
    const params = hashParams(window.location.hash);
    if (next === undefined || next === "") params.delete(name);
    else params.set(name, next);
    const query = params.toString();
    window.history.replaceState(
      window.history.state,
      "",
      `${window.location.pathname}${window.location.search}${path}${query ? `?${query}` : ""}`,
    );
    setValue(next || undefined);
  };
  return [value, set];
}
