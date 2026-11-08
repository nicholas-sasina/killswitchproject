// @vitest-environment jsdom
import { webcrypto } from "node:crypto";
import { Buffer } from "node:buffer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { createPeopleAuth } from "../auth";
import { App } from "../App";
import { LocalSignIn } from "./SignIn";

const config = {
  peopleDomain: "people.example.test",
  peopleClientId: "public-client",
};
function idToken(email = "person@example.test") {
  return `eyJhbGciOiJSUzI1NiJ9.${Buffer.from(
    JSON.stringify({
      email,
      token_use: "id",
      aud: config.peopleClientId,
      exp: Math.floor(Date.now() / 1000) + 3600,
    }),
  ).toString("base64url")}.test-signature`;
}
function tokens(extra = {}) {
  return {
    id_token: idToken(),
    access_token: crypto.randomUUID(),
    refresh_token: crypto.randomUUID(),
    ...extra,
  };
}
function setup(response = tokens()) {
  const navigate = vi.fn();
  const fetcher = vi
    .fn()
    .mockImplementation(async () => new Response(JSON.stringify(response)));
  const auth = createPeopleAuth(config, { fetcher, navigate });
  async function callback(state) {
    await auth.signIn();
    const url = new URL(navigate.mock.calls.at(-1)[0]);
    window.history.replaceState(
      null,
      "",
      `/?code=test-code&state=${state ?? url.searchParams.get("state")}`,
    );
    await auth.completeCallback();
  }
  return { auth, navigate, fetcher, callback, response };
}

beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
  sessionStorage.clear();
  localStorage.clear();
  window.history.replaceState(null, "", "/");
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("people sign-in screen", () => {
  const dashboardConfig = { burnUrl: "https://burn.example.test", ...config };

  it("gates dashboard requests with one sign-in button", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    await act(async () => {
      render(<App config={dashboardConfig} />);
    });
    expect(
      screen.getByRole("button", { name: "Continue with email" }).disabled,
    ).toBe(false);
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.queryByRole("navigation", { name: "Console" })).toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("completes a callback once under StrictMode, displays email, and sends ID tokens to the API", async () => {
    const fixture = setup();
    await fixture.auth.signIn();
    const state = new URL(fixture.navigate.mock.calls[0][0]).searchParams.get(
      "state",
    );
    window.history.replaceState(null, "", `/?code=test-code&state=${state}`);
    const fetcher = vi.fn(async (input, init) => {
      const url = new URL(String(input));
      if (url.pathname === "/oauth2/token")
        return new Response(JSON.stringify(fixture.response));
      expect(window.location.search).toBe("");
      expect(new Headers(init.headers).get("Authorization")).toBe(
        `Bearer ${fixture.response.id_token}`,
      );
      expect(new Headers(init.headers).has("x-burn-demo-token")).toBe(false);
      const data =
        url.pathname === "/roles"
          ? { roles: [] }
          : url.pathname === "/instances"
            ? { instances: [] }
            : url.pathname === "/flags"
              ? { flags: [] }
              : { events: [], agents: [] };
      return new Response(JSON.stringify(data));
    });
    vi.stubGlobal("fetch", fetcher);
    await act(async () => {
      render(
        <StrictMode>
          <App config={dashboardConfig} />
        </StrictMode>,
      );
    });
    expect(
      fetcher.mock.calls.filter(([url]) =>
        String(url).endsWith("/oauth2/token"),
      ),
    ).toHaveLength(1);
    fireEvent.click(
      screen.getByRole("button", { name: "Signed in as person@example.test" }),
    );
    expect(screen.getByText("person@example.test")).toBeTruthy();
    expect(
      new URL(screen.getByRole("link", { name: "Sign out" }).href).pathname,
    ).toBe("/logout");
    expect(screen.getByRole("navigation", { name: "Console" })).toBeTruthy();
    expect(window.location.search).toBe("");
  });

  it("returns to the sign-in screen when refresh fails and removes the dashboard data", async () => {
    const fixture = setup();
    await fixture.callback();
    const fetcher = vi.fn(
      async (input) =>
        new Response("private response", {
          status: String(input).endsWith("/oauth2/token") ? 400 : 401,
        }),
    );
    vi.stubGlobal("fetch", fetcher);
    await act(async () => {
      render(<App config={dashboardConfig} />);
    });
    expect(
      screen.getByRole("button", { name: "Continue with email" }),
    ).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "Console" })).toBeNull();
    expect(sessionStorage.getItem("burn.people.session")).toBeNull();
  });

  it("keeps one shared instance poll after sign-in, across pages and old links", async () => {
    const fixture = setup();
    await fixture.callback();
    vi.useFakeTimers();
    const fetcher = vi.fn(async (input) => {
      const path = new URL(String(input)).pathname;
      return new Response(
        JSON.stringify(
          path === "/instances"
            ? { instances: [] }
            : path === "/roles"
              ? { roles: [] }
              : path === "/flags"
                ? { flags: [] }
                : { events: [], agents: [] },
        ),
      );
    });
    vi.stubGlobal("fetch", fetcher);
    const count = () =>
      fetcher.mock.calls.filter(
        ([url]) => new URL(String(url)).pathname === "/instances",
      ).length;
    await act(async () => {
      render(<App config={dashboardConfig} />);
    });
    expect(
      screen.getByRole("link", { name: "Overview", current: "page" }),
    ).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(count()).toBe(5);
    await act(async () => {
      window.location.hash = "#/burned";
      window.dispatchEvent(new Event("hashchange"));
    });
    expect(
      screen.getByRole("link", { name: /^Incidents/, current: "page" }),
    ).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    // The same poll, not a second one for the new page.
    expect(count()).toBe(6);
    window.location.hash = "";
  });

  it("names the new public config variables without requesting a demo token in production", async () => {
    await act(async () => {
      render(<App config={{ burnUrl: "" }} />);
    });
    expect(screen.getByText("VITE_BURN_API_URL")).toBeTruthy();
    expect(screen.getByText("VITE_BURN_PEOPLE_DOMAIN")).toBeTruthy();
    expect(screen.getByText("VITE_BURN_PEOPLE_CLIENT_ID")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Continue with email" }),
    ).toBeNull();
  });
});

describe("sign-in screen states", () => {
  const dashboardConfig = { burnUrl: "https://burn.example.test", ...config };

  it("links back to the website", async () => {
    await act(async () => {
      render(<App config={dashboardConfig} />);
    });
    expect(screen.getByRole("link", { name: "Back to the website" }).href).toBe(
      "http://localhost:5174/",
    );
  });

  it("reads Opening sign-in and disables the button while the redirect starts", async () => {
    // PKCE never finishes hashing, so sign-in stays on its way out.
    vi.stubGlobal("crypto", {
      getRandomValues: (bytes) => webcrypto.getRandomValues(bytes),
      subtle: { digest: () => new Promise(() => {}) },
    });
    await act(async () => {
      render(<App config={dashboardConfig} />);
    });
    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: "Continue with email" }),
      );
    });
    const button = screen.getByRole("button", { name: "Opening sign-in" });
    expect(button.disabled).toBe(true);
  });

  it("says Signing you in while the callback is exchanged", async () => {
    const fixture = setup();
    await fixture.auth.signIn();
    const state = new URL(fixture.navigate.mock.calls[0][0]).searchParams.get(
      "state",
    );
    window.history.replaceState(null, "", `/?code=test-code&state=${state}`);
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})),
    );
    await act(async () => {
      render(<App config={dashboardConfig} />);
    });
    expect(
      screen.getByRole("heading", { name: "Signing you in" }),
    ).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain(
      "Finishing your sign-in with Amazon Cognito.",
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says what failed and offers Try again", async () => {
    window.history.replaceState(null, "", "/?code=test-code&state=unknown");
    await act(async () => {
      render(<App config={dashboardConfig} />);
    });
    expect(screen.getByRole("alert").textContent).toBe(
      "Sign-in failed. The callback was invalid or expired. Please sign in again.",
    );
    expect(screen.getByRole("button", { name: "Try again" }).disabled).toBe(
      false,
    );
  });

  it("shows the local development screen once per tab", async () => {
    const view = render(
      <LocalSignIn>
        <p>Dashboard</p>
      </LocalSignIn>,
    );
    expect(
      screen.getByRole("heading", { name: "Local development" }),
    ).toBeTruthy();
    expect(screen.queryByText("Dashboard")).toBeNull();
    act(() => {
      fireEvent.click(
        screen.getByRole("button", { name: "Continue as admin" }),
      );
    });
    expect(screen.getByText("Dashboard")).toBeTruthy();
    view.unmount();
    render(
      <LocalSignIn>
        <p>Dashboard</p>
      </LocalSignIn>,
    );
    expect(screen.getByText("Dashboard")).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Continue as admin" }),
    ).toBeNull();
  });
});
