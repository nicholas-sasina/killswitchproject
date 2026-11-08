export interface PeopleConfig {
  peopleDomain?: string;
  peopleClientId?: string;
}

export interface PeopleSession {
  id_token: string;
  access_token: string;
  refresh_token?: string;
  email: string;
}

export interface ApiAuth {
  idToken: () => string | undefined;
  refresh: () => Promise<string | undefined>;
  clear: () => void;
}

const sessionKey = "burn.people.session";
const pendingKey = "burn.people.pkce";

function domainUrl(domain: string) {
  const url = new URL(domain.includes("://") ? domain : `https://${domain}`);
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  )
    throw new Error("Check VITE_BURN_PEOPLE_DOMAIN.");
  return url.origin;
}

function base64url(bytes: Uint8Array) {
  return btoa(String.fromCharCode(...bytes))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/, "");
}

export async function createPkce() {
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const state = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const hash = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  return { verifier, state, challenge: base64url(new Uint8Array(hash)) };
}

function sessionFrom(
  value: unknown,
  clientId: string,
  previousRefresh?: string,
): PeopleSession {
  const data = value as Partial<PeopleSession>;
  if (
    !data ||
    typeof data.id_token !== "string" ||
    typeof data.access_token !== "string"
  )
    throw new Error("Invalid sign-in response.");
  const segment = data.id_token.split(".")[1];
  const base64 = segment.replaceAll("-", "+").replaceAll("_", "/");
  const claims = JSON.parse(
    new TextDecoder().decode(
      Uint8Array.from(atob(base64), (char) => char.charCodeAt(0)),
    ),
  );
  // The API verifies the signature. These claims identify the session shown by the UI.
  if (
    claims.token_use !== "id" ||
    claims.aud !== clientId ||
    typeof claims.email !== "string" ||
    typeof claims.exp !== "number" ||
    claims.exp <= Date.now() / 1000
  )
    throw new Error("Invalid sign-in response.");
  return {
    id_token: data.id_token,
    access_token: data.access_token,
    email: claims.email,
    refresh_token:
      typeof data.refresh_token === "string"
        ? data.refresh_token
        : previousRefresh,
  };
}

export function createPeopleAuth(
  config: PeopleConfig,
  options: {
    storage?: Storage;
    fetcher?: typeof fetch;
    navigate?: (url: string) => void;
  } = {},
) {
  const storage = options.storage ?? window.sessionStorage;
  const fetcher = options.fetcher ?? fetch;
  const navigate =
    options.navigate ?? ((url: string) => window.location.assign(url));
  const clientId = config.peopleClientId ?? "";
  const context = `${config.peopleDomain ?? ""}:${clientId}`;
  let session: PeopleSession | undefined;
  try {
    const saved = JSON.parse(storage.getItem(sessionKey) ?? "null");
    if (
      saved?.context === context &&
      typeof saved.tokens?.id_token === "string" &&
      typeof saved.tokens?.email === "string"
    )
      session = saved.tokens;
  } catch {
    /* A missing or invalid saved session starts signed out. */
  }
  const listeners = new Set<() => void>();
  let revision = 0;
  let refreshing: Promise<string | undefined> | undefined;
  let callback: Promise<void> | undefined;
  const redirectUri = () => new URL("/", window.location.origin).href;
  function clear() {
    revision++;
    storage.removeItem(sessionKey);
    storage.removeItem(pendingKey);
    session = undefined;
    for (const listener of listeners) listener();
  }
  function save(next: PeopleSession) {
    storage.setItem(sessionKey, JSON.stringify({ context, tokens: next }));
    session = next;
    for (const listener of listeners) listener();
  }
  function logoutUrl() {
    const url = new URL(`${domainUrl(config.peopleDomain ?? "")}/logout`);
    url.search = new URLSearchParams({
      client_id: clientId,
      logout_uri: redirectUri(),
    }).toString();
    return url.href;
  }
  async function exchange(body: URLSearchParams, previousRefresh?: string) {
    try {
      const response = await fetcher(
        `${domainUrl(config.peopleDomain ?? "")}/oauth2/token`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
            Accept: "application/json",
          },
          body,
          cache: "no-store",
          credentials: "omit",
          signal: AbortSignal.timeout(10_000),
        },
      );
      if (!response.ok) throw new Error();
      return sessionFrom(await response.json(), clientId, previousRefresh);
    } catch {
      throw new Error("Sign-in could not be completed. Please sign in again.");
    }
  }
  async function completeCallback() {
    const url = new URL(window.location.href);
    if (
      !url.searchParams.has("code") &&
      !url.searchParams.has("error") &&
      !url.searchParams.has("state")
    )
      return;
    // Remove authorization data before any asynchronous work or rendering of an error.
    window.history.replaceState(null, "", url.pathname);
    const pending = storage.getItem(pendingKey);
    clear();
    let request;
    try {
      request = JSON.parse(pending ?? "null");
    } catch {
      /* Rejected below. */
    }
    if (
      url.pathname !== "/" ||
      !request ||
      request.context !== context ||
      !request.state ||
      url.searchParams.getAll("state").length !== 1 ||
      url.searchParams.get("state") !== request.state ||
      typeof request.verifier !== "string"
    ) {
      throw new Error("Sign-in state did not match. Please sign in again.");
    }
    if (
      url.searchParams.has("error") ||
      url.searchParams.getAll("code").length !== 1 ||
      !url.searchParams.get("code")
    ) {
      throw new Error("Sign-in was not completed. Please sign in again.");
    }
    const expectedRevision = revision;
    const next = await exchange(
      new URLSearchParams({
        grant_type: "authorization_code",
        client_id: clientId,
        code: url.searchParams.get("code")!,
        code_verifier: request.verifier,
        redirect_uri: redirectUri(),
      }),
    );
    if (revision === expectedRevision) save(next);
  }
  return {
    getSession: () => session,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    idToken: () => session?.id_token,
    clear,
    async signIn() {
      const url = new URL(
        `${domainUrl(config.peopleDomain ?? "")}/oauth2/authorize`,
      );
      if (!clientId) throw new Error("Set VITE_BURN_PEOPLE_CLIENT_ID.");
      const pkce = await createPkce();
      storage.setItem(
        pendingKey,
        JSON.stringify({ context, verifier: pkce.verifier, state: pkce.state }),
      );
      url.search = new URLSearchParams({
        response_type: "code",
        client_id: clientId,
        redirect_uri: redirectUri(),
        scope: "openid email profile",
        state: pkce.state,
        code_challenge: pkce.challenge,
        code_challenge_method: "S256",
      }).toString();
      navigate(url.href);
    },
    completeCallback: () => (callback ??= completeCallback()),
    refresh() {
      if (refreshing) return refreshing;
      const expectedRevision = revision;
      const previousRefresh = session?.refresh_token;
      refreshing = (async () => {
        try {
          if (!previousRefresh) throw new Error();
          const next = await exchange(
            new URLSearchParams({
              grant_type: "refresh_token",
              client_id: clientId,
              refresh_token: previousRefresh,
            }),
            previousRefresh,
          );
          if (revision !== expectedRevision) return undefined;
          save(next);
          return next.id_token;
        } catch {
          if (revision === expectedRevision) clear();
          return undefined;
        }
      })().finally(() => {
        refreshing = undefined;
      });
      return refreshing;
    },
    logoutUrl,
    signOut() {
      clear();
      navigate(logoutUrl());
    },
  };
}

export type PeopleAuth = ReturnType<typeof createPeopleAuth>;
