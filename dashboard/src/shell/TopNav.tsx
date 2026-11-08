import { routeHref, sectionOf, type Route, type Section } from "../router";
import { Mark } from "../ui";
import { PersonMenu } from "./PersonMenu";

const SECTIONS: { section: Section; label: string }[] = [
  { section: "overview", label: "Overview" },
  { section: "roles", label: "Roles" },
  { section: "incidents", label: "Incidents" },
  { section: "credentials", label: "Credentials" },
];

/**
 * The white bar inset from the page edges, as on the website: the mark, the
 * four places, then the region, Add agents and the signed-in person.
 */
export function TopNav({
  route,
  openIncidents,
  region,
  email,
  logoutUrl,
  onAddAgents,
  onSignOut,
}: {
  route: Route;
  /** Open incidents; undefined until the data has arrived. */
  openIncidents?: number;
  region?: string;
  email?: string;
  logoutUrl?: string;
  onAddAgents: () => void;
  onSignOut: () => void;
}) {
  const current = sectionOf(route);
  return (
    <header className="topbar wrap">
      <div className="topbar-inner">
        <a
          href="#/overview"
          className="topbar-home"
          aria-label="Burn, overview"
        >
          <Mark size="nav" />
        </a>
        <span className="topbar-rule" aria-hidden="true" />
        <nav className="topbar-nav" aria-label="Console">
          {SECTIONS.map(({ section, label }) => (
            <a
              key={section}
              href={routeHref({ page: section })}
              aria-current={current === section ? "page" : undefined}
              aria-label={
                section === "incidents" && openIncidents !== undefined
                  ? `${label}, ${openIncidents} open`
                  : undefined
              }
            >
              {label}
              {section === "incidents" && openIncidents !== undefined && (
                <span
                  aria-hidden="true"
                  className={`topbar-count num${openIncidents ? " is-open" : ""}`}
                >
                  {openIncidents}
                </span>
              )}
            </a>
          ))}
        </nav>
        <span className="topbar-spacer" />
        {region && <span className="topbar-region">{region}</span>}
        <button
          type="button"
          className="btn btn-dark btn-sm topbar-add"
          onClick={onAddAgents}
        >
          Add agents
        </button>
        <PersonMenu email={email} logoutUrl={logoutUrl} onSignOut={onSignOut} />
      </div>
    </header>
  );
}
