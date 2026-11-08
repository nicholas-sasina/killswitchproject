import type {
  BlastRadius,
  BurnRequest,
  BurnResult,
  EventsResponse,
  LedgerResponse,
  Role,
  RoleSpec,
  EnrollToken,
  Instance,
  ScopedBurnRequest,
  ScopedBurnResult,
} from "./contracts";
import type { ApiAuth, PeopleConfig } from "./auth";

export interface ApiConfig extends PeopleConfig {
  burnUrl: string;
  /**
   * The AgentCore Gateway's MCP endpoint, which burn run connects agents
   * to. The dashboard never calls it; it only shows it in join commands.
   */
  gatewayUrl?: string;
  localDev?: boolean;
  demoToken?: string;
}

export function readConfig(): ApiConfig {
  return {
    burnUrl: import.meta.env.VITE_BURN_API_URL?.trim() ?? "",
    gatewayUrl: import.meta.env.VITE_BURN_GATEWAY_URL?.trim() ?? "",
    peopleDomain: import.meta.env.VITE_BURN_PEOPLE_DOMAIN?.trim() ?? "",
    peopleClientId: import.meta.env.VITE_BURN_PEOPLE_CLIENT_ID?.trim() ?? "",
    localDev: import.meta.env.VITE_BURN_LOCAL_DEV === "1",
    demoToken:
      import.meta.env.VITE_BURN_LOCAL_DEV === "1"
        ? (import.meta.env.VITE_BURN_DEMO_TOKEN?.trim() ?? "")
        : "",
  };
}

export function createApi(
  config: ApiConfig,
  onResponse: (headers: Headers) => void,
  fetcher: typeof fetch = fetch,
  auth?: ApiAuth,
) {
  async function request<T>(
    base: string,
    path: string,
    init: RequestInit = {},
    retried = false,
  ): Promise<T> {
    if (!base)
      throw new Error(
        "API URL is missing. Set it in dashboard/.env.local and restart Vite.",
      );
    const headers = new Headers(init.headers);
    const token = auth?.idToken();
    headers.delete("x-burn-demo-token");
    headers.delete("Authorization");
    if (config.localDev)
      headers.set("x-burn-demo-token", config.demoToken ?? "");
    else if (token) headers.set("Authorization", `Bearer ${token}`);
    else throw new Error("Sign in to access the Burn API.");
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
    if (response.status === 401 && !config.localDev) {
      if (!retried && !init.signal?.aborted) {
        const current = auth?.idToken();
        let refreshed: string | undefined;
        try {
          refreshed =
            current && current !== token ? current : await auth?.refresh();
        } catch {
          /* Return to sign-in below. */
        }
        if (refreshed) return request<T>(base, path, init, true);
      }
      if (!init.signal?.aborted) auth?.clear();
      throw new Error("Session expired. Sign in again.");
    }
    if (!response.ok) {
      throw new Error(
        response.status === 401 || response.status === 403
          ? `Request denied (HTTP ${response.status}).${config.localDev ? " Check the local development token." : ""}`
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
    roles: (signal?: AbortSignal) =>
      request<{ roles: Role[] }>(config.burnUrl, "/roles", { signal }),
    createRole: (body: RoleSpec) =>
      request<Role>(config.burnUrl, "/roles", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    saveRole: (body: RoleSpec) =>
      request<Role>(config.burnUrl, `/roles/${encodeURIComponent(body.name)}`, {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    createToken: (name: string, body: { uses: number; ttl: number }) =>
      request<EnrollToken & { token: string }>(
        config.burnUrl,
        `/roles/${encodeURIComponent(name)}/tokens`,
        { method: "POST", body: JSON.stringify(body) },
      ),
    instances: (role?: string, signal?: AbortSignal) =>
      request<{ instances: Instance[] }>(
        config.burnUrl,
        `/instances${role ? `?role=${encodeURIComponent(role)}` : ""}`,
        { signal },
      ),
    burnScope: (body: ScopedBurnRequest) =>
      request<ScopedBurnResult>(config.burnUrl, "/burns", {
        method: "POST",
        body: JSON.stringify(body),
      }),
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
  };
}

export type BurnApi = ReturnType<typeof createApi>;
