import type {
  BlastRadius,
  BurnBySourceRequest,
  BurnBySourceResult,
  BurnRequest,
  BurnResult,
  EventsResponse,
  LedgerResponse,
  Role,
  RoleSpec,
  EnrollToken,
  Flag,
  FlagResolution,
  FlagResolveResult,
  Instance,
  ScopedBurnRequest,
  ScopedBurnResult,
} from "./contracts";
import type { ApiAuth, PeopleConfig } from "./auth";

export interface ApiConfig extends PeopleConfig {
  burnUrl: string;
  localDev?: boolean;
  demoToken?: string;
}

/**
 * A request the API answered with an error status. The message is the same
 * plain sentence as before; `status` lets a caller word a 404 or a 409 itself.
 */
export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function readConfig(): ApiConfig {
  return {
    burnUrl: import.meta.env.VITE_BURN_API_URL?.trim() ?? "",
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
      throw new ApiError("Session expired. Sign in again.", 401);
    }
    if (!response.ok) {
      throw new ApiError(
        response.status === 401 || response.status === 403
          ? `Request denied (HTTP ${response.status}).${config.localDev ? " Check the local development token." : ""}`
          : `API request failed (HTTP ${response.status}).`,
        response.status,
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
    events: (since?: string, signal?: AbortSignal, limit?: number) => {
      const query = new URLSearchParams();
      if (since) query.set("since", since);
      if (limit) query.set("limit", String(limit));
      const search = query.toString();
      return request<EventsResponse>(
        config.burnUrl,
        `/events${search ? `?${search}` : ""}`,
        { signal },
      );
    },
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
    instance: (sessionId: string, signal?: AbortSignal) =>
      request<Instance>(
        config.burnUrl,
        `/instances/${encodeURIComponent(sessionId)}`,
        { signal },
      ),
    revokeToken: (tokenId: string) =>
      request<EnrollToken>(
        config.burnUrl,
        `/tokens/${encodeURIComponent(tokenId)}`,
        { method: "DELETE" },
      ),
    burnBySource: (body: BurnBySourceRequest) =>
      request<BurnBySourceResult>(config.burnUrl, "/burn/by-source", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    flags: (signal?: AbortSignal) =>
      request<{ flags: Flag[] }>(config.burnUrl, "/flags", { signal }),
    resolveFlag: (body: FlagResolution) =>
      request<FlagResolveResult>(config.burnUrl, "/flags/resolve", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  };
}

export type BurnApi = ReturnType<typeof createApi>;
