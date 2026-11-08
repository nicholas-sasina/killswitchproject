/**
 * The console: the navigation, the page the hash names, and the dialogs
 * any page can open. Pages read their data from the shared provider.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createApi, type ApiConfig } from "../api";
import type { PeopleAuth } from "../auth";
import { ConsoleDataProvider, useConsole } from "../data/ConsoleData";
import { MockBanner, mockDataReducer } from "../MockBanner";
import { useRoute, routeHref, type Route } from "../router";
import { ConfigurationNotice, signOutLocal } from "../components/SignIn";
import { Notice, PulseContext } from "../ui";
import { AgentPage } from "../pages/agent/AgentPage";
import { CredentialsPage } from "../pages/credentials/CredentialsPage";
import { IncidentsPage } from "../pages/incidents/IncidentsPage";
import { OverviewPage } from "../pages/overview/OverviewPage";
import { AddAgentsDialog } from "../pages/roles/AddAgentsDialog";
import { RolePage } from "../pages/roles/RolePage";
import { RolesPage } from "../pages/roles/RolesPage";
import { TopNav } from "./TopNav";
import "./shell.css";

function PageFor({ route }: { route: Route }) {
  switch (route.page) {
    case "overview":
      return <OverviewPage />;
    case "roles":
      return <RolesPage />;
    case "role":
      return <RolePage key={route.name} name={route.name} />;
    case "agent":
      return <AgentPage key={route.sessionId} sessionId={route.sessionId} />;
    case "incidents":
      return <IncidentsPage />;
    case "credentials":
      return <CredentialsPage />;
  }
}

/** What the page can't show when a shared request fails. */
function ConnectionNotice() {
  const { eventsError, instancesError } = useConsole();
  const message = eventsError
    ? `${eventsError} The console keeps the last data it received while it reconnects.`
    : instancesError
      ? `Agents could not be loaded: ${instancesError} The list may be incomplete.`
      : "";
  return message ? (
    <div className="page-notices wrap">
      <Notice tone="error">{message}</Notice>
    </div>
  ) : null;
}

function Frame({
  route,
  auth,
  mock,
  adding,
  onCloseAdding,
}: {
  route: Route;
  auth?: PeopleAuth;
  mock: boolean;
  adding: { role?: string } | null;
  onCloseAdding: () => void;
}) {
  const data = useConsole();
  const main = useRef<HTMLElement>(null);
  // A new page starts at the top.
  const href = routeHref(route);
  useEffect(() => {
    window.scrollTo?.(0, 0);
  }, [href]);
  return (
    <PulseContext value={data.pulses}>
      <div className="console">
        <a
          className="skip-link"
          href={href}
          onClick={(event) => {
            event.preventDefault();
            main.current?.focus();
          }}
        >
          Skip to content
        </a>
        <MockBanner visible={mock} />
        <TopNav
          route={route}
          openIncidents={
            data.instancesLoaded && data.flagsLoaded
              ? data.incidentCounts.open
              : undefined
          }
          region={data.region ?? (data.config.localDev ? "local" : undefined)}
          email={data.email}
          logoutUrl={auth?.logoutUrl()}
          onAddAgents={() => data.openAddAgents()}
          onSignOut={() => (auth ? auth.signOut() : signOutLocal())}
        />
        <main ref={main} id="main" tabIndex={-1} className="console-main">
          {!data.configured ? (
            <div className="wrap console-setup">
              <ConfigurationNotice />
            </div>
          ) : (
            <>
              <ConnectionNotice />
              <PageFor route={route} />
            </>
          )}
        </main>
        {adding && (
          <AddAgentsDialog role={adding.role} onClose={onCloseAdding} />
        )}
      </div>
    </PulseContext>
  );
}

export function Shell({
  config,
  auth,
}: {
  config: ApiConfig;
  auth?: PeopleAuth;
}) {
  const [mock, setMock] = useState(false);
  const observeResponse = useCallback(
    (headers: Headers) =>
      setMock((current) => mockDataReducer(current, headers)),
    [],
  );
  // Keyed on the fields, not the object: a caller may pass a fresh config
  // each render, and a new client would restart every poll.
  const api = useMemo(
    () => createApi(config, observeResponse, fetch, auth),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      config.burnUrl,
      config.demoToken,
      config.localDev,
      config.gatewayUrl,
      auth,
      observeResponse,
    ],
  );
  const configured = Boolean(
    config.burnUrl && (config.localDev ? config.demoToken : auth?.idToken()),
  );
  const route = useRoute();
  const [adding, setAdding] = useState<{ role?: string } | null>(null);
  const openAddAgents = useCallback((role?: string) => setAdding({ role }), []);
  return (
    <ConsoleDataProvider
      api={api}
      config={config}
      configured={configured}
      email={auth?.getSession()?.email}
      openAddAgents={openAddAgents}
    >
      <Frame
        route={route}
        auth={auth}
        mock={mock}
        adding={adding}
        onCloseAdding={() => setAdding(null)}
      />
    </ConsoleDataProvider>
  );
}
