// @vitest-environment jsdom
import { webcrypto, createHash } from "node:crypto";
import { Buffer } from "node:buffer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPeopleAuth, createPkce } from "./auth";

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
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("Cognito sign-in", () => {
  it("generates independent random state and verifier and the S256 challenge", async () => {
    const first = await createPkce(),
      second = await createPkce();
    expect(first.verifier).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(first.state).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(first.verifier).not.toBe(first.state);
    expect(second.verifier).not.toBe(first.verifier);
    expect(second.state).not.toBe(first.state);
    expect(first.challenge).toBe(
      createHash("sha256").update(first.verifier).digest("base64url"),
    );
  });

  it("redirects to the managed login with public client, scopes, PKCE and no verifier in the URL", async () => {
    const { auth, navigate } = setup();
    await auth.signIn();
    const url = new URL(navigate.mock.calls[0][0]);
    expect(url.origin + url.pathname).toBe(
      "https://people.example.test/oauth2/authorize",
    );
    const pending = JSON.parse(sessionStorage.getItem("burn.people.pkce"));
    expect(url.searchParams.get("state")).toBe(pending.state);
    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("client_id")).toBe(config.peopleClientId);
    expect(url.searchParams.get("scope")).toBe("openid email profile");
    expect(url.searchParams.get("redirect_uri")).toBe(
      `${window.location.origin}/`,
    );
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.get("code_challenge")).toBe(
      createHash("sha256").update(pending.verifier).digest("base64url"),
    );
    expect(url.href).not.toContain(pending.verifier);
    expect(localStorage.length).toBe(0);
  });

  it("checks state, strips the callback before exchanging, stores tokens in the tab and exchanges once", async () => {
    const fixture = setup();
    await fixture.auth.signIn();
    const pending = JSON.parse(sessionStorage.getItem("burn.people.pkce"));
    const code = crypto.randomUUID();
    window.history.replaceState(
      null,
      "",
      `/?code=${code}&state=${pending.state}`,
    );
    fixture.fetcher.mockImplementation(async () => {
      expect(window.location.search).toBe("");
      expect(sessionStorage.getItem("burn.people.pkce")).toBeNull();
      return new Response(JSON.stringify(fixture.response));
    });
    await Promise.all([
      fixture.auth.completeCallback(),
      fixture.auth.completeCallback(),
    ]);
    expect(fixture.fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fixture.fetcher.mock.calls[0];
    expect(url).toBe("https://people.example.test/oauth2/token");
    expect(new Headers(init.headers).get("Content-Type")).toBe(
      "application/x-www-form-urlencoded",
    );
    expect(Object.fromEntries(new URLSearchParams(init.body))).toEqual({
      grant_type: "authorization_code",
      client_id: config.peopleClientId,
      code,
      code_verifier: pending.verifier,
      redirect_uri: `${window.location.origin}/`,
    });
    expect(fixture.auth.getSession().email).toBe("person@example.test");
    expect(fixture.auth.idToken()).toBe(fixture.response.id_token);
    expect(createPeopleAuth(config).idToken()).toBe(fixture.response.id_token);
    expect(localStorage.length).toBe(0);
  });

  it.each(["wrong-state", ""])(
    "rejects a mismatched or missing callback state (%s) without exchanging a code",
    async (state) => {
      const fixture = setup();
      await expect(fixture.callback(state)).rejects.toThrow(
        "state did not match",
      );
      expect(fixture.fetcher).not.toHaveBeenCalled();
      expect(window.location.search).toBe("");
      expect(fixture.auth.getSession()).toBeUndefined();
      expect(sessionStorage.length).toBe(0);
    },
  );

  it("rejects an unsolicited callback and strips a provider error without echoing it", async () => {
    const fixture = setup();
    window.history.replaceState(null, "", "/?code=secret-code&state=unknown");
    await expect(fixture.auth.completeCallback()).rejects.toThrow(
      "state did not match",
    );
    expect(window.location.search).toBe("");
    const next = setup();
    await next.auth.signIn();
    const state = new URL(next.navigate.mock.calls[0][0]).searchParams.get(
      "state",
    );
    window.history.replaceState(
      null,
      "",
      `/?error=denied&error_description=private-detail&state=${state}`,
    );
    await expect(next.auth.completeCallback()).rejects.toThrow(
      "Sign-in was not completed",
    );
    expect(window.location.search).toBe("");
    expect(next.fetcher).not.toHaveBeenCalled();
  });

  it("reports token exchange failures without echoing tokens or response bodies", async () => {
    const fixture = setup();
    const secret = crypto.randomUUID();
    fixture.fetcher.mockResolvedValue(new Response(secret, { status: 400 }));
    await expect(fixture.callback()).rejects.toThrow(
      "Sign-in could not be completed. Please sign in again.",
    );
    expect(sessionStorage.length).toBe(0);
    expect(window.location.search).toBe("");
  });

  it("shares refreshes, retains the old refresh token when omitted, and accepts rotated tokens", async () => {
    const fixture = setup();
    await fixture.callback();
    const refreshed = tokens({
      id_token: idToken("new@example.test"),
      refresh_token: undefined,
    });
    fixture.fetcher.mockImplementation(
      async () => new Response(JSON.stringify(refreshed)),
    );
    await Promise.all([fixture.auth.refresh(), fixture.auth.refresh()]);
    expect(fixture.fetcher).toHaveBeenCalledTimes(2);
    expect(
      Object.fromEntries(
        new URLSearchParams(fixture.fetcher.mock.calls[1][1].body),
      ),
    ).toEqual({
      grant_type: "refresh_token",
      client_id: config.peopleClientId,
      refresh_token: fixture.response.refresh_token,
    });
    expect(fixture.auth.getSession().refresh_token).toBe(
      fixture.response.refresh_token,
    );
    expect(fixture.auth.getSession().email).toBe("new@example.test");
    const rotated = tokens();
    fixture.fetcher.mockImplementation(
      async () => new Response(JSON.stringify(rotated)),
    );
    await fixture.auth.refresh();
    expect(fixture.auth.getSession().refresh_token).toBe(rotated.refresh_token);
  });

  it("clears the session after refresh failure and never resurrects a signed-out session", async () => {
    const fixture = setup();
    await fixture.callback();
    fixture.fetcher.mockResolvedValue(new Response("private", { status: 400 }));
    expect(await fixture.auth.refresh()).toBeUndefined();
    expect(fixture.auth.getSession()).toBeUndefined();
    const next = setup();
    await next.callback();
    let finish;
    next.fetcher.mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const refreshing = next.auth.refresh();
    next.auth.clear();
    finish(new Response(JSON.stringify(tokens())));
    expect(await refreshing).toBeUndefined();
    expect(next.auth.getSession()).toBeUndefined();
  });

  it("clears only Burn sign-in state and redirects through managed logout without token parameters", async () => {
    const fixture = setup();
    await fixture.callback();
    sessionStorage.setItem("unrelated", "keep");
    const expected = fixture.auth.logoutUrl();
    fixture.auth.signOut();
    expect(fixture.navigate).toHaveBeenLastCalledWith(expected);
    const url = new URL(expected);
    expect(url.origin + url.pathname).toBe(
      "https://people.example.test/logout",
    );
    expect(Object.fromEntries(url.searchParams)).toEqual({
      client_id: config.peopleClientId,
      logout_uri: `${window.location.origin}/`,
    });
    expect(fixture.auth.getSession()).toBeUndefined();
    expect(sessionStorage.getItem("burn.people.session")).toBeNull();
    expect(sessionStorage.getItem("unrelated")).toBe("keep");
  });

  it("does not reuse another app client's saved session or log any token", async () => {
    const logs = ["log", "warn", "error", "debug", "info"].map((method) =>
      vi.spyOn(console, method),
    );
    const fixture = setup();
    await fixture.callback();
    await fixture.auth.refresh();
    expect(
      createPeopleAuth({
        ...config,
        peopleClientId: "different-client",
      }).getSession(),
    ).toBeUndefined();
    fixture.auth.signOut();
    for (const log of logs) expect(log).not.toHaveBeenCalled();
  });
});
