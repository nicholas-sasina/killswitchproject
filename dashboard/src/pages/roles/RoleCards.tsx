import { Fragment, type ReactNode } from "react";
import type { Role } from "../../contracts";
import { roleHref } from "../../router";
import { Card, Chip, EmptyState, Facts } from "../../ui";
import { aRole, protections, reach } from "./model";

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="role-may-section">
      <h3 className="role-may-label">{label}</h3>
      <div className="role-may-value">{children}</div>
    </div>
  );
}

/** "api_key, aws_key", each in mono. */
function Keys({ names }: { names: string[] }) {
  return (
    <>
      {names.map((name, index) => (
        <Fragment key={name}>
          {index > 0 && ", "}
          <span className="id">{name}</span>
        </Fragment>
      ))}
    </>
  );
}

/** Role names as links to their pages. */
function RoleLinks({ names }: { names: string[] }) {
  return (
    <>
      {names.map((name, index) => (
        <Fragment key={name}>
          {index > 0 && ", "}
          <a className="text-link" href={roleHref(name)}>
            {name}
          </a>
        </Fragment>
      ))}
    </>
  );
}

const SHARED: Record<NonNullable<Role["references"]>, string> = {
  replace: "shared, delivered as a reference",
  report: "shared, recorded but not replaced",
  off: "shared",
};

/**
 * "What a worker may do": its tools, its keys, where it can reach, and
 * which roles it may start or message.
 */
export function MayDoCard({
  name,
  role,
  loaded,
  shared,
}: {
  name: string;
  role?: Role;
  /** Whether /roles has answered. */
  loaded: boolean;
  /** Shared keys its agents were seen holding. */
  shared: string[];
}) {
  if (!role)
    return (
      <Card title={`What ${aRole(name)} may do`} pad="narrow">
        <EmptyState>
          {loaded
            ? "Burn has no record of this role's permissions."
            : "Loading the role…"}
        </EmptyState>
      </Card>
    );
  const own = role.credentials ?? [];
  const where = reach(role.egress);
  const spawn = role.may_spawn ?? [];
  const message = role.may_message ?? [];
  return (
    <Card
      title={`What ${aRole(name)} may do`}
      pad="narrow"
      className="role-may"
    >
      <Section label="Tools">
        {role.tools.length ? (
          <div className="role-chips">
            {role.tools.map((tool) => (
              <Chip key={tool}>{tool}</Chip>
            ))}
          </div>
        ) : (
          "No tools."
        )}
      </Section>
      <Section label="Keys">
        {own.length || shared.length ? (
          <ul className="role-keys-list">
            {own.length > 0 && (
              <li>
                <Keys names={own} />{" "}
                <span className="role-muted">
                  each {name} gets its own copy
                </span>
              </li>
            )}
            {shared.length > 0 && (
              <li>
                <Keys names={shared} />{" "}
                <span className="role-muted">
                  {SHARED[role.references ?? "replace"]}
                </span>
              </li>
            )}
          </ul>
        ) : (
          "No keys of its own."
        )}
      </Section>
      <Section label="Can reach">
        {where.hosts.length > 0 && <Keys names={where.hosts} />}
        {where.text}
      </Section>
      <Section label="Can start">
        {spawn.length ? (
          <>
            <RoleLinks names={spawn} /> agents, with at most {aRole(name)}
            &apos;s tools
          </>
        ) : (
          "No other agents."
        )}
      </Section>
      {message.length > 0 && (
        <Section label="Can message">
          <RoleLinks names={message} />
        </Section>
      )}
    </Card>
  );
}

/** "Protections": references, canaries, burn on canary use, replace. */
export function ProtectionsCard({ role }: { role?: Role }) {
  return (
    <Card title="Protections" pad="narrow" className="role-protections">
      {role ? (
        <Facts
          rows={protections(role).map((row) => ({
            label: row.label,
            note: row.note,
            value: row.value,
          }))}
        />
      ) : (
        <EmptyState>Shown once Burn has the role&apos;s record.</EmptyState>
      )}
    </Card>
  );
}
