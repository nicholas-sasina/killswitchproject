import { useCallback, useState } from "react";
import { useConsole } from "../../data/ConsoleData";
import { BurnDialog, type BurnTarget } from "../../features/burn/BurnDialog";
import { joinable } from "../../fleet";
import { roleCounts, type ConsoleAgent } from "../../model";
import { useHashParam } from "../../router";
import { Columns, EmptyState, Hero } from "../../ui";
import { AgentsCard } from "./AgentsCard";
import { filterOf, managedIn, rolePlural, type Filter } from "./model";
import "./roles.css";

/** The hero's paragraph: what a role is, who owns it, where it's defined. */
function Lede({
  name,
  owner,
  where,
}: {
  name: string;
  owner?: string | null;
  where: string;
}) {
  const place =
    where === "burn.yaml" ? <span className="id">burn.yaml</span> : where;
  return (
    <>
      A role is a kind of agent: every {name} gets these permissions, its own
      identity and its own copies of its keys.
      {owner ? (
        <>
          {" "}
          Owned by {owner}
          {where && <>, managed in {place}</>}.
        </>
      ) : where ? (
        <> Managed in {place}.</>
      ) : null}
    </>
  );
}

/**
 * One role (#/roles/<name>): what it is, its agents by state, what it may
 * do, its protections and its join tokens. Built from the Roles board.
 */
export function RolePage({ name }: { name: string }) {
  const { model, rolesLoaded, instancesLoaded, openAddAgents } = useConsole();
  const [show, setShow] = useHashParam("show");
  const filter = filterOf(show);
  const onFilter = useCallback(
    (next: Filter) => setShow(next === "running" ? undefined : next),
    [setShow],
  );
  const [target, setTarget] = useState<BurnTarget>();
  const onBurn = useCallback(
    (agent: ConsoleAgent) => setTarget({ scope: "agent", agent }),
    [],
  );

  const group = model.roleByName.get(name);
  const role = group?.role;
  const counts = roleCounts(model, name);
  const plural = rolePlural(name);
  const canAdd = role ? joinable(role) : Boolean(group);
  const missing = rolesLoaded && instancesLoaded && !group;

  if (missing)
    return (
      <>
        <Hero
          crumbs={[{ label: "Roles", href: "#/roles" }, { label: name }]}
          title={name}
          lede="Burn has no role by this name. It may have been removed, or the link may be mistyped."
          className="role-hero"
        />
        <Columns>
          <section className="card card-wide">
            <EmptyState
              action={
                <a className="btn btn-dark" href="#/roles">
                  See every role
                </a>
              }
            >
              No role named <span className="id">{name}</span>.
            </EmptyState>
          </section>
        </Columns>
      </>
    );

  return (
    <div className="role-page">
      <Hero
        crumbs={[{ label: "Roles", href: "#/roles" }, { label: name }]}
        title={name}
        lede={<Lede name={name} owner={role?.owner} where={managedIn(role)} />}
        statSize="md"
        stats={[
          { label: "Running", value: counts.running },
          { label: "Burned today", value: counts.burnedToday, tone: "ember" },
        ]}
        actions={
          <>
            {canAdd && (
              <button
                className="btn btn-white"
                type="button"
                onClick={() => openAddAgents(name)}
              >
                Add {plural}
              </button>
            )}
            <button
              className="btn btn-ghost-ember"
              type="button"
              disabled={!counts.running}
              title={counts.running ? undefined : `No ${plural} running`}
              onClick={() => setTarget({ scope: "role", role: name })}
            >
              Burn every {name}
            </button>
          </>
        }
        className="role-hero"
      />
      <Columns>
        <AgentsCard
          role={name}
          counts={counts}
          filter={filter}
          onFilter={onFilter}
          canAdd={canAdd}
          onBurn={onBurn}
        />
      </Columns>
      {target && (
        <BurnDialog target={target} onClose={() => setTarget(undefined)} />
      )}
    </div>
  );
}
