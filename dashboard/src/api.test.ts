import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";

function setup(
  response = () => new Response(JSON.stringify({ events: [], agents: [] })),
) {
  const config = {
    burnUrl: "https://burn.example.test/",
    demoToken: crypto.randomUUID(),
    leakTestUrl: "https://test.example.test",
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
    await api.encodings();
    await api.leakTest({ agent_id: id, encoding: "plain" });
    expect(fetcher.mock.calls.map((call) => String(call[0]))).toEqual([
      `https://burn.example.test/agents/${id}/ledger`,
      `https://burn.example.test/burn/${id}/blast-radius`,
      "https://burn.example.test/burn",
      "https://test.example.test/encodings",
      "https://test.example.test/leak-test",
    ]);
    for (const [, init] of fetcher.mock.calls.slice(0, 3)) {
      expect(new Headers(init?.headers).get("x-burn-demo-token")).toBe(
        config.demoToken,
      );
    }
    for (const [, init] of fetcher.mock.calls.slice(3)) {
      expect(new Headers(init?.headers).has("x-burn-demo-token")).toBe(false);
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

  it("reports network and invalid JSON failures without echoing server content", async () => {
    const { api, fetcher } = setup(() => new Response("invalid json"));
    await expect(api.events()).rejects.toThrow("API returned invalid JSON.");
    fetcher.mockRejectedValueOnce(new Error(crypto.randomUUID()));
    await expect(api.events()).rejects.toThrow("API unreachable.");
  });
});
