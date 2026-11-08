import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";

function setup(
  response = () => new Response(JSON.stringify({ events: [], agents: [] })),
) {
  const config = {
    burnUrl: "https://burn.example.test/",
    demoToken: crypto.randomUUID(),
  };
  const fetcher = vi
    .fn<typeof fetch>()
    .mockImplementation(async () => response());
  const observe = vi.fn();
  return { api: createApi(config, observe, fetcher), config, fetcher, observe };
}

describe("API client", () => {
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
      "Request denied (HTTP 403). Check the demo token.",
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
