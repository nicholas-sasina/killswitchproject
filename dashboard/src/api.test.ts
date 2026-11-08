import { afterEach, describe, expect, it, vi } from "vitest";
import { createApi, readConfig } from "./api";

afterEach(() => vi.unstubAllEnvs());

function setup(
  response = () => new Response(JSON.stringify({ events: [], agents: [] })),
) {
  const config = {
    burnUrl: "https://burn.example.test/",
    localDev: true,
    demoToken: crypto.randomUUID(),
  };
  const fetcher = vi
    .fn<typeof fetch>()
    .mockImplementation(async () => response());
  const observe = vi.fn();
  return { api: createApi(config, observe, fetcher), config, fetcher, observe };
}

describe("API client", () => {
  it("saves the replacement setting with PUT to the named role", async () => {
    const { api, fetcher } = setup();
    const spec = {
      name: "worker",
      tools: [],
      managed_by: "ui" as const,
      replace_on_burn: true,
    };
    await api.saveRole(spec);
    expect(fetcher.mock.calls[0][0]).toBe(
      "https://burn.example.test/roles/worker",
    );
    expect(fetcher.mock.calls[0][1]?.method).toBe("PUT");
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual(spec);
  });
  it("sends the configured token and encodes the since cursor", async () => {
    const { api, fetcher, config } = setup();
    const cursor = `${crypto.randomUUID()}+/?`;
    await api.events(cursor);
    const [url, init] = fetcher.mock.calls[0];
    expect(new URL(String(url)).searchParams.get("since")).toBe(cursor);
    expect(new Headers(init?.headers).get("x-burn-demo-token")).toBe(
      config.demoToken,
    );
    expect(init?.cache).toBe("no-store");
    await api.events();
    expect(fetcher.mock.calls[1][0]).toBe("https://burn.example.test/events");
  });

  it("routes each contract endpoint and sends JSON with no extra payload fields", async () => {
    const { api, fetcher, config } = setup();
    const id = `agent-${crypto.randomUUID()}`;
    await api.ledger(id);
    await api.blastRadius(id);
    await api.burn({ agent_id: id, reason: "Containment requested" });
    expect(fetcher.mock.calls.map((call) => String(call[0]))).toEqual([
      `https://burn.example.test/agents/${id}/ledger`,
      `https://burn.example.test/burn/${id}/blast-radius`,
      "https://burn.example.test/burn",
    ]);
    for (const [, init] of fetcher.mock.calls) {
      expect(new Headers(init?.headers).get("x-burn-demo-token")).toBe(
        config.demoToken,
      );
    }
    const burn = fetcher.mock.calls[2][1];
    expect(burn?.method).toBe("POST");
    expect(JSON.parse(String(burn?.body))).toEqual({
      agent_id: id,
      reason: "Containment requested",
    });
    expect(new Headers(burn?.headers).get("Content-Type")).toBe(
      "application/json",
    );
  });

  it("observes mock headers on failed responses without displaying response bodies", async () => {
    const privateText = crypto.randomUUID();
    const { api, observe } = setup(
      () =>
        new Response(privateText, {
          status: 403,
          headers: { "x-burn-mock": "true" },
        }),
    );
    await expect(api.events()).rejects.toThrow(
      "Request denied (HTTP 403). Check the local development token.",
    );
    expect(observe.mock.calls[0][0].get("x-burn-mock")).toBe("true");
  });

  it("routes role enrollment, filtered instances and scoped burns with contract payloads", async () => {
    const { api, fetcher, config } = setup();
    const signal = new AbortController().signal;
    const spec = {
      name: "worker",
      tools: ["read_ticket"],
      managed_by: "ui" as const,
      canaries: true,
      auto_burn: true,
    };
    await api.roles(signal);
    await api.instances();
    await api.instances("worker+/?", signal);
    await api.createRole(spec);
    await api.createToken("worker/?", { uses: 2, ttl: 600 });
    await api.burnScope({
      scope: "instance",
      id: "session_123",
      reason: "Containment requested",
    });
    await api.burnScope({ scope: "role", id: "worker" });
    expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
      "https://burn.example.test/roles",
      "https://burn.example.test/instances",
      "https://burn.example.test/instances?role=worker%2B%2F%3F",
      "https://burn.example.test/roles",
      "https://burn.example.test/roles/worker%2F%3F/tokens",
      "https://burn.example.test/burns",
      "https://burn.example.test/burns",
    ]);
    expect(
      fetcher.mock.calls
        .slice(3)
        .map(([, init]) => JSON.parse(String(init?.body))),
    ).toEqual([
      spec,
      { uses: 2, ttl: 600 },
      { scope: "instance", id: "session_123", reason: "Containment requested" },
      { scope: "role", id: "worker" },
    ]);
    for (const [, init] of fetcher.mock.calls) {
      expect(new Headers(init?.headers).get("x-burn-demo-token")).toBe(
        config.demoToken,
      );
      expect(init?.cache).toBe("no-store");
    }
    for (const [, init] of fetcher.mock.calls.slice(3)) {
      expect(init?.method).toBe("POST");
      expect(new Headers(init?.headers).get("Content-Type")).toBe(
        "application/json",
      );
    }
  });

  it("reports network and invalid JSON failures without echoing server content", async () => {
    const { api, fetcher } = setup(() => new Response("invalid json"));
    await expect(api.events()).rejects.toThrow("API returned invalid JSON.");
    fetcher.mockRejectedValueOnce(new Error(crypto.randomUUID()));
    await expect(api.events()).rejects.toThrow("API unreachable.");
  });
});

describe("people API authentication", () => {
  function setupPeople(statuses = [200]) {
    let token: string | undefined = crypto.randomUUID();
    const firstToken = token;
    const nextToken = crypto.randomUUID();
    const auth = {
      idToken: () => token,
      refresh: vi.fn(async (): Promise<string | undefined> => {
        token = nextToken;
        return token;
      }),
      clear: vi.fn(() => {
        token = undefined;
      }),
    };
    const fetcher = vi.fn<typeof fetch>().mockImplementation(
      async () =>
        new Response(JSON.stringify({ events: [], agents: [] }), {
          status: statuses.shift() ?? 200,
        }),
    );
    const api = createApi(
      { burnUrl: "https://burn.example.test", demoToken: crypto.randomUUID() },
      () => {},
      fetcher,
      auth,
    );
    return { api, auth, fetcher, firstToken, nextToken };
  }

  it("sends only the ID bearer token, never a demo token outside local-dev mode", async () => {
    const fixture = setupPeople();
    await fixture.api.events();
    const headers = new Headers(fixture.fetcher.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe(`Bearer ${fixture.firstToken}`);
    expect(headers.has("x-burn-demo-token")).toBe(false);
    expect(fixture.auth.refresh).not.toHaveBeenCalled();
  });

  it("refreshes on 401 and retries the original POST once with the new ID token", async () => {
    const fixture = setupPeople([401, 200]);
    const body = { scope: "role" as const, id: "worker" };
    await fixture.api.burnScope(body);
    expect(fixture.auth.refresh).toHaveBeenCalledTimes(1);
    expect(fixture.fetcher).toHaveBeenCalledTimes(2);
    expect(
      fixture.fetcher.mock.calls.map(([, init]) =>
        new Headers(init?.headers).get("Authorization"),
      ),
    ).toEqual([`Bearer ${fixture.firstToken}`, `Bearer ${fixture.nextToken}`]);
    for (const [, init] of fixture.fetcher.mock.calls) {
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init?.body))).toEqual(body);
    }
    expect(fixture.auth.clear).not.toHaveBeenCalled();
  });

  it("clears sign-in after the retry is denied without refreshing or retrying again", async () => {
    const fixture = setupPeople([401, 401]);
    await expect(fixture.api.events()).rejects.toThrow(
      "Session expired. Sign in again.",
    );
    expect(fixture.auth.refresh).toHaveBeenCalledTimes(1);
    expect(fixture.fetcher).toHaveBeenCalledTimes(2);
    expect(fixture.auth.clear).toHaveBeenCalledTimes(1);
  });

  it.each(["missing", "rejected"])(
    "clears sign-in when the refresh is %s",
    async (mode) => {
      const fixture = setupPeople([401]);
      if (mode === "missing") fixture.auth.refresh.mockResolvedValue(undefined);
      else
        fixture.auth.refresh.mockRejectedValue(new Error(crypto.randomUUID()));
      await expect(fixture.api.events()).rejects.toThrow(
        "Session expired. Sign in again.",
      );
      expect(fixture.fetcher).toHaveBeenCalledTimes(1);
      expect(fixture.auth.clear).toHaveBeenCalledTimes(1);
    },
  );

  it("does not refresh or clear the session for a forbidden request", async () => {
    const fixture = setupPeople([403]);
    await expect(fixture.api.events()).rejects.toThrow(
      "Request denied (HTTP 403).",
    );
    expect(fixture.auth.refresh).not.toHaveBeenCalled();
    expect(fixture.auth.clear).not.toHaveBeenCalled();
  });

  it("does not fall back to demo credentials when a people session is missing", async () => {
    const fetcher = vi.fn<typeof fetch>();
    const api = createApi(
      { burnUrl: "https://burn.example.test", demoToken: crypto.randomUUID() },
      () => {},
      fetcher,
    );
    await expect(api.events()).rejects.toThrow(
      "Sign in to access the Burn API.",
    );
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("uses only demo credentials with explicit local-dev mode and does not refresh a 401", async () => {
    const fixture = setupPeople([401]);
    const demoToken = crypto.randomUUID();
    const api = createApi(
      { burnUrl: "http://localhost:8700", localDev: true, demoToken },
      () => {},
      fixture.fetcher,
      fixture.auth,
    );
    await expect(api.events()).rejects.toThrow(
      "Check the local development token.",
    );
    const headers = new Headers(fixture.fetcher.mock.calls[0][1]?.headers);
    expect(headers.get("x-burn-demo-token")).toBe(demoToken);
    expect(headers.has("Authorization")).toBe(false);
    expect(fixture.auth.refresh).not.toHaveBeenCalled();
  });

  it("opts in to local development only with VITE_BURN_LOCAL_DEV=1", () => {
    vi.stubEnv("VITE_BURN_DEMO_TOKEN", crypto.randomUUID());
    vi.stubEnv("VITE_BURN_PEOPLE_DOMAIN", "people.example.test");
    vi.stubEnv("VITE_BURN_PEOPLE_CLIENT_ID", "public-client");
    for (const flag of ["", "0", "true"]) {
      vi.stubEnv("VITE_BURN_LOCAL_DEV", flag);
      expect(readConfig().localDev).toBe(false);
      expect(readConfig().demoToken).toBe("");
    }
    vi.stubEnv("VITE_BURN_LOCAL_DEV", "1");
    expect(readConfig().localDev).toBe(true);
    expect(readConfig().demoToken).toBe(import.meta.env.VITE_BURN_DEMO_TOKEN);
    expect(readConfig().peopleDomain).toBe("people.example.test");
    expect(readConfig().peopleClientId).toBe("public-client");
  });

  it("reads the gateway URL for the join commands, blank when unset", () => {
    vi.stubEnv("VITE_BURN_GATEWAY_URL", "");
    expect(readConfig().gatewayUrl).toBe("");
    vi.stubEnv(
      "VITE_BURN_GATEWAY_URL",
      " https://gw-1.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp ",
    );
    expect(readConfig().gatewayUrl).toBe(
      "https://gw-1.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
    );
  });
});
