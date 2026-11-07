import type {
  BlastRadius,
  BurnRequest,
  BurnResult,
  EncodingsResponse,
  EventsResponse,
  LeakTestRequest,
  LeakTestResult,
  LedgerResponse,
} from "./contracts";

export interface ApiConfig {
  burnUrl: string;
  demoToken: string;
  leakTestUrl: string;
}

export function readConfig(): ApiConfig {
  return {
    burnUrl: import.meta.env.VITE_BURN_API_URL?.trim() ?? "",
    demoToken: import.meta.env.VITE_BURN_DEMO_TOKEN?.trim() ?? "",
    leakTestUrl: import.meta.env.VITE_LEAKTEST_URL?.trim() ?? "",
  };
}

export function createApi(
  config: ApiConfig,
  onResponse: (headers: Headers) => void,
  fetcher: typeof fetch = fetch,
) {
  async function request<T>(
    base: string,
    path: string,
    init: RequestInit = {},
    authenticated = true,
  ): Promise<T> {
    if (!base)
      throw new Error(
        "API URL is missing. Set it in dashboard/.env.local and restart Vite.",
      );
    const headers = new Headers(init.headers);
    if (authenticated) headers.set("x-burn-demo-token", config.demoToken);
    headers.set("Accept", "application/json");
    if (init.body) headers.set("Content-Type", "application/json");

    let response: Response;
    try {
      response = await fetcher(`${base.replace(/\/+$/, "")}${path}`, {
        ...init,
        headers,
        cache: "no-store",
        signal: init.signal
          ? AbortSignal.any([init.signal, AbortSignal.timeout(10_000)])
          : AbortSignal.timeout(10_000),
      });
    } catch (error) {
      if (init.signal?.aborted) throw error;
      throw new Error(
        "API unreachable. Check the service, URL and browser CORS settings.",
      );
    }
    // Inspect even failed responses so mock data can never appear as a live service.
    onResponse(response.headers);
    if (!response.ok) {
      throw new Error(
        response.status === 401 || response.status === 403
          ? `Request denied (HTTP ${response.status}). Check the demo token.`
          : `API request failed (HTTP ${response.status}).`,
      );
    }
    try {
      return (await response.json()) as T;
    } catch {
      throw new Error(
        "API returned invalid JSON. Check the configured service.",
      );
    }
  }

  return {
    events: (since?: string, signal?: AbortSignal) =>
      request<EventsResponse>(
        config.burnUrl,
        `/events${since ? `?since=${encodeURIComponent(since)}` : ""}`,
        { signal },
      ),
    ledger: (id: string, signal?: AbortSignal) =>
      request<LedgerResponse>(
        config.burnUrl,
        `/agents/${encodeURIComponent(id)}/ledger`,
        { signal },
      ),
    burn: (body: BurnRequest) =>
      request<BurnResult>(config.burnUrl, "/burn", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    blastRadius: (id: string, signal?: AbortSignal) =>
      request<BlastRadius>(
        config.burnUrl,
        `/burn/${encodeURIComponent(id)}/blast-radius`,
        { signal },
      ),
    encodings: (signal?: AbortSignal) =>
      request<EncodingsResponse>(
        config.leakTestUrl,
        "/encodings",
        { signal },
        false,
      ),
    leakTest: (body: LeakTestRequest) =>
      request<LeakTestResult>(
        config.leakTestUrl,
        "/leak-test",
        {
          method: "POST",
          body: JSON.stringify(body),
        },
        false,
      ),
  };
}

export type BurnApi = ReturnType<typeof createApi>;
