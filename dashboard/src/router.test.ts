import { describe, expect, it } from "vitest";
import {
  agentHref,
  instanceHref,
  parseHash,
  roleHref,
  routeFromHash,
  routeHref,
  sectionOf,
} from "./router";

describe("console routes", () => {
  it("names each page by its hash", () => {
    expect(routeFromHash("#/overview")).toEqual({ page: "overview" });
    expect(routeFromHash("#/roles")).toEqual({ page: "roles" });
    expect(routeFromHash("#/roles/worker")).toEqual({
      page: "role",
      name: "worker",
    });
    expect(routeFromHash("#/agents/sess_b2e1")).toEqual({
      page: "agent",
      sessionId: "sess_b2e1",
    });
    expect(routeFromHash("#/incidents")).toEqual({ page: "incidents" });
    expect(routeFromHash("#/credentials")).toEqual({ page: "credentials" });
  });

  it("sends old links to the page that replaced them", () => {
    expect(parseHash("#/burned")).toEqual({
      route: { page: "incidents" },
      canonical: false,
    });
    expect(parseHash("#/alerts")).toEqual({
      route: { page: "overview" },
      canonical: false,
    });
    expect(parseHash("#/overview?instance=sess_1")).toEqual({
      route: { page: "agent", sessionId: "sess_1" },
      canonical: false,
    });
  });

  it("opens the overview for an empty or unknown hash", () => {
    expect(parseHash("")).toEqual({
      route: { page: "overview" },
      canonical: false,
    });
    expect(routeFromHash("#/nowhere")).toEqual({ page: "overview" });
    expect(routeFromHash("#/agents")).toEqual({ page: "roles" });
  });

  it("round-trips names that need escaping", () => {
    const href = agentHref("session_a/b c");
    expect(href).toBe("#/agents/session_a%2Fb%20c");
    expect(routeFromHash(href)).toEqual({
      page: "agent",
      sessionId: "session_a/b c",
    });
    expect(parseHash(roleHref("code-reader")).canonical).toBe(true);
    expect(routeHref({ page: "role", name: "worker" })).toBe("#/roles/worker");
  });

  it("keeps instance links pointing at the agent page", () => {
    expect(instanceHref("sess_1")).toBe("#/agents/sess_1");
  });

  it("puts a role and an agent under Roles in the navigation", () => {
    expect(sectionOf({ page: "role", name: "worker" })).toBe("roles");
    expect(sectionOf({ page: "agent", sessionId: "s" })).toBe("roles");
    expect(sectionOf({ page: "incidents" })).toBe("incidents");
  });
});
