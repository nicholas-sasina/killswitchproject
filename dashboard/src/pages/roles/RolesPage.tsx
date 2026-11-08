import { useMemo } from "react";
import { useConsole } from "../../data/ConsoleData";
import { roleHref } from "../../router";
import {
  Card,
  CardNote,
  Columns,
  EmptyState,
  Hero,
  Notice,
  Square,
} from "../../ui";
import { rolesList, type ListRow } from "./model";
import "./roles.css";

/** One role: its name and line, its squares, its counts. */
function RoleRow({ row }: { row: ListRow }) {
  return (
    <li className="roles-row">
      <div className="roles-row-role">
        <a className="roles-row-name" href={roleHref(row.name)}>
          {row.name}
        </a>
        {row.line && <span className="roles-row-line">{row.line}</span>}
      </div>
      {/* The counts beside say the same in words; the role page lists them. */}
      <div className="roles-squares" aria-hidden="true">
        {row.squares.map((square) => (
          <Square
            key={square.key}
            state={square.state}
            label={square.label}
            pulseKey={square.key}
          />
        ))}
      </div>
      <div className="roles-row-counts">
        <span className="roles-row-running num">{row.running} running</span>
        <span className={`roles-row-note num${row.hot ? " is-ember" : ""}`}>
          {row.note}
        </span>
      </div>
    </li>
  );
}

function kinds(n: number) {
  return `${n} ${n === 1 ? "kind" : "kinds"} of agent`;
}

/**
 * Every role (#/roles): a row per role with a square per agent running or
 * burned today, as the Overview draws them, and the role's counts. Roles
 * with nothing running are listed too, so every role has a way in.
 */
export function RolesPage() {
  const {
    model,
    rolesLoaded,
    rolesError,
    instancesLoaded,
    instancesError,
    openAddAgents,
  } = useConsole();
  const rows = useMemo(() => rolesList(model), [model]);
  const loading =
    !rows.length &&
    ((!rolesLoaded && !rolesError) || (!instancesLoaded && !instancesError));
  const failed = !rows.length && rolesError && instancesError;

  return (
    <div className="roles-page">
      <Hero
        title="Roles"
        lede="A role is a kind of agent. Each running copy is an agent with its own identity and keys."
        statSize="md"
        stats={[
          { label: "Running", value: model.counts.running },
          {
            label: "Burned today",
            value: model.counts.burnedToday,
            tone: "ember",
          },
        ]}
        actions={
          <button
            className="btn btn-white"
            type="button"
            onClick={() => openAddAgents()}
          >
            Add agents
          </button>
        }
      />
      <Columns>
        <Card
          title="Roles"
          sub={loading || failed ? undefined : kinds(rows.length)}
          className="roles-card"
          aside={
            <div className="roles-legend" aria-label="Legend">
              <span className="roles-legend-item">
                <span className="square square-running square-12" />
                Running
              </span>
              <span className="roles-legend-item">
                <span className="square square-burned square-12" />
                Burned today
              </span>
            </div>
          }
        >
          {failed ? (
            <Notice tone="error">
              {`Roles could not be loaded: ${rolesError}`}
            </Notice>
          ) : loading ? (
            <EmptyState>Loading roles…</EmptyState>
          ) : !rows.length ? (
            <EmptyState>
              No roles yet. Add agents to create a role and its first join
              token.
            </EmptyState>
          ) : (
            <>
              {rolesError && (
                <Notice tone="error">
                  {`Role records could not be loaded: ${rolesError}. Roles with no agents may be missing.`}
                </Notice>
              )}
              {instancesError && (
                <Notice tone="error">
                  {`Agents could not be loaded: ${instancesError}. The counts may be short.`}
                </Notice>
              )}
              <ul className="roles-rows">
                {rows.map((row) => (
                  <RoleRow key={row.name} row={row} />
                ))}
              </ul>
              <CardNote>
                Each row is a role; each square is one of its agents, running
                now or burned today. Select a role to see all its agents,
                including finished ones, and what it may do.
              </CardNote>
            </>
          )}
        </Card>
      </Columns>
    </div>
  );
}
