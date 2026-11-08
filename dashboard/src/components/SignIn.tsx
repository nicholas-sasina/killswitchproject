import {
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import type { ApiConfig } from "../api";
import { createPeopleAuth, type PeopleAuth } from "../auth";
import { canHandOff, handOff } from "../handoff";
import { ErrorMessage } from "./common";
import "./signin.css";

/** Where "Back to the website" goes. */
export const SITE_URL: string =
  import.meta.env.VITE_SITE_URL?.trim() || "http://localhost:5174/";

const FLAME =
  "M10.2 1C11.3 4.6 16 6.9 16 12.6A7 7 0 0 1 2 12.9c0-3 1.6-5.1 3.4-6.5.1 2.3 1.1 3.8 2.7 4.4C7.4 7.2 8.4 3.8 10.2 1Z";

export function ConfigurationNotice() {
  return (
    <section className="setup panel" role="status">
      <h2>Connect the dashboard</h2>
      <p>
        Set <code>VITE_BURN_API_URL</code>, <code>VITE_BURN_PEOPLE_DOMAIN</code>{" "}
        and <code>VITE_BURN_PEOPLE_CLIENT_ID</code> in{" "}
        <code>dashboard/.env.local</code>, then restart Vite. For local mocks,
        set <code>VITE_BURN_LOCAL_DEV=1</code> and{" "}
        <code>VITE_BURN_DEMO_TOKEN</code>. See <code>.env.example</code>.
      </p>
    </section>
  );
}

/** Cognito sent the person back here, with a code or an error, to finish signing in. */
function isReturning(): boolean {
  const params = new URLSearchParams(window.location.search);
  return params.has("code") || params.has("error") || params.has("state");
}

/** The website's mark, so the sign-in screen reads as the room after it. */
function SiteMark() {
  return (
    <div className="signin-mark">
      <svg viewBox="0 0 18 22" aria-hidden="true">
        <path d={FLAME} fill="currentColor" />
      </svg>
      <span className="signin-word">
        BURN<span className="signin-dot">.</span>
      </span>
    </div>
  );
}

/** The night stage and white card that every sign-in state shares. */
function Stage({ busy, children }: { busy?: boolean; children: ReactNode }) {
  return (
    <div className="signin" data-theme="dark">
      <main
        className="signin-card"
        data-theme="light"
        aria-labelledby="signin-title"
        aria-busy={busy || undefined}
      >
        <SiteMark />
        {children}
        <p className="signin-back">
          <a href={SITE_URL}>Back to the website</a>
        </p>
      </main>
    </div>
  );
}

export function SignIn({
  config,
  children,
}: {
  config: ApiConfig;
  children: (auth: PeopleAuth) => ReactNode;
}) {
  const auth = useMemo(
    () => createPeopleAuth(config),
    [config.peopleDomain, config.peopleClientId],
  );
  const session = useSyncExternalStore(auth.subscribe, auth.getSession);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Read before the callback clears the URL: is this load the way back from Cognito?
  const [returning] = useState(isReturning);
  const [entered, setEntered] = useState(false);
  const configured = Boolean(
    config.burnUrl && config.peopleDomain && config.peopleClientId,
  );
  useEffect(() => {
    let active = true;
    setReady(false);
    setError("");
    auth
      .completeCallback()
      .catch(() => {
        if (active)
          setError(
            "Sign-in failed. The callback was invalid or expired. Please sign in again.",
          );
      })
      .finally(() => {
        if (active) setReady(true);
      });
    return () => {
      active = false;
    };
  }, [auth]);
  const signedIn = ready && configured && Boolean(session);
  // Someone who has just signed in watches the card hand over to the
  // dashboard. A reload with a saved session goes straight in.
  const animate = returning && canHandOff();
  useEffect(() => {
    if (signedIn && animate && !entered) handOff(() => setEntered(true));
  }, [signedIn, animate, entered]);
  if (signedIn && (!animate || entered)) return children(auth);
  // A saved session is being checked, which takes a moment. Show the empty
  // page rather than flash the card on every reload.
  if (configured && session && !ready && !returning) return null;

  if (!configured)
    return (
      <Stage>
        <h1 id="signin-title" className="signin-title">
          Sign in to Burn
        </h1>
        <ConfigurationNotice />
      </Stage>
    );

  if (returning && (!ready || signedIn))
    return (
      <Stage busy>
        <div role="status">
          <h1 id="signin-title" className="signin-title">
            Signing you in
          </h1>
          <p className="signin-lede">
            Finishing your sign-in with Amazon Cognito.
          </p>
        </div>
        <div className="signin-progress" aria-hidden="true">
          <span />
        </div>
      </Stage>
    );

  return (
    <Stage busy={busy}>
      <h1 id="signin-title" className="signin-title">
        Sign in to Burn
      </h1>
      <p className="signin-lede">
        Use your work email. Your Burn admin sends the invite.
      </p>
      <ErrorMessage message={error} />
      <button
        className="signin-button"
        type="button"
        disabled={!ready || busy}
        onClick={() => {
          setBusy(true);
          setError("");
          void auth.signIn().catch(() => {
            setError(
              "Could not start sign-in. Check the people sign-in configuration and browser session storage.",
            );
            setBusy(false);
          });
        }}
      >
        {busy ? "Opening sign-in" : error ? "Try again" : "Continue with email"}
      </button>
      <p className="signin-note">
        You&rsquo;ll sign in on Amazon Cognito&rsquo;s secure page and come
        straight back.
      </p>
    </Stage>
  );
}
