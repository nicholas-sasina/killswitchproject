import {
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import type { ApiConfig } from "../api";
import { createPeopleAuth, type PeopleAuth } from "../auth";
import { ErrorMessage } from "./common";
import { Brand } from "./Brand";

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
  if (ready && configured && session) return children(auth);
  return (
    <div className="app-frame">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <Brand />
        </div>
      </aside>
      <div className="content">
        <main>
          {!configured ? (
            <ConfigurationNotice />
          ) : (
            <section className="setup panel" aria-labelledby="signin-title">
              <h1 id="signin-title">Sign in to Burn</h1>
              <ErrorMessage message={error} />
              <button
                className="secondary-button"
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
                {ready ? "Sign in" : "Completing sign-in…"}
              </button>
            </section>
          )}
        </main>
      </div>
    </div>
  );
}
