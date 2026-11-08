/** Cognito Hosted UI Authorization Code + PKCE. Tokens remain in sessionStorage. */
export interface SignInConfig { domain: string; clientId: string; redirectUri: string }
const KEY = "burn-human-auth";
const TRANSIENT = "burn-human-pkce";
export interface SignInSession { accessToken: string; owner: string; expiresAt: number }
const encode = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
function config(): SignInConfig {
  return { domain: import.meta.env.VITE_COGNITO_DOMAIN?.trim() || "",
    clientId: import.meta.env.VITE_COGNITO_CLIENT_ID?.trim() || "",
    redirectUri: window.location.origin + "/" };
}
export function signInConfigured() { const c = config(); return Boolean(c.domain && c.clientId); }
export function currentSession(): SignInSession | null {
  try {
    const item = sessionStorage.getItem(KEY);
    const session = item ? JSON.parse(item) as SignInSession : null;
    return session && session.expiresAt > Date.now() + 5000 ? session : null;
  } catch { return null; }
}
export async function startSignIn(): Promise<void> {
  const c = config();
  if (!c.domain || !c.clientId) throw new Error("Cognito sign-in configuration is missing");
  const verifier = encode(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = encode(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
  const state = encode(crypto.getRandomValues(new Uint8Array(16)));
  sessionStorage.setItem(TRANSIENT, JSON.stringify({ verifier, state }));
  const url = new URL(c.domain.replace(/\/+$/, "") + "/oauth2/authorize");
  url.search = new URLSearchParams({ response_type: "code", client_id: c.clientId,
    redirect_uri: c.redirectUri, scope: "openid email profile", state,
    code_challenge_method: "S256", code_challenge: challenge }).toString();
  window.location.assign(url.toString());
}
export async function completeSignIn(): Promise<SignInSession | null> {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  if (params.has("error")) { window.history.replaceState({}, "", "/"); throw new Error("Cognito sign-in was not completed"); }
  if (!code) return currentSession();
  const raw = sessionStorage.getItem(TRANSIENT);
  sessionStorage.removeItem(TRANSIENT);
  window.history.replaceState({}, "", "/");
  if (!raw) throw new Error("Sign-in state missing; try again");
  const { verifier, state } = JSON.parse(raw) as { verifier: string; state: string };
  if (!state || params.get("state") !== state) throw new Error("Sign-in state mismatch");
  const c = config();
  const response = await fetch(c.domain.replace(/\/+$/, "") + "/oauth2/token", {
    method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "authorization_code", client_id: c.clientId,
      code, redirect_uri: c.redirectUri, code_verifier: verifier }),
  });
  if (!response.ok) throw new Error("Cognito token exchange failed");
  const tokens = await response.json() as { access_token: string; expires_in: number; id_token: string };
  if (!tokens.access_token || !tokens.id_token) throw new Error("Invalid Cognito response");
  // This display claim is informational only; API Gateway validates every API access token.
  const payload = JSON.parse(atob(tokens.id_token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))) as
    { email?: string; "cognito:username"?: string; sub?: string };
  const session = { accessToken: tokens.access_token,
    owner: payload.email || payload["cognito:username"] || payload.sub || "Signed-in user",
    expiresAt: Date.now() + tokens.expires_in * 1000 };
  sessionStorage.setItem(KEY, JSON.stringify(session));
  return session;
}
export function signOut() { sessionStorage.removeItem(KEY); sessionStorage.removeItem(TRANSIENT); }
