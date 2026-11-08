import type { Instance } from "../contracts";
import { Status, Timestamp } from "./common";
import { instanceHref } from "../router";
import { ReplacementInfo } from "./ReplacementInfo";

interface LineageRow {
  instance: Instance;
  depth: number;
  context: boolean;
}

// Include ancestors from other roles so a child still appears beneath its parent.
export function roleLineage(
  selected: Instance[],
  instances: Instance[],
): LineageRow[] {
  const byId = new Map(
    instances.map((instance) => [instance.session_id, instance]),
  );
  const selectedIds = new Set(selected.map((instance) => instance.session_id));
  const included = new Set<string>();
  for (const instance of selected) {
    let current: Instance | undefined = instance;
    while (current && !included.has(current.session_id)) {
      included.add(current.session_id);
      current = current.parent_session
        ? byId.get(current.parent_session)
        : undefined;
    }
  }
  const children = new Map<string, Instance[]>();
  const roots: Instance[] = [];
  for (const instance of instances) {
    if (!included.has(instance.session_id)) continue;
    if (instance.parent_session && included.has(instance.parent_session)) {
      const siblings = children.get(instance.parent_session) ?? [];
      siblings.push(instance);
      children.set(instance.parent_session, siblings);
    } else roots.push(instance);
  }
  const rows: LineageRow[] = [];
  const seen = new Set<string>();
  function visit(instance: Instance, depth: number) {
    if (seen.has(instance.session_id)) return;
    seen.add(instance.session_id);
    rows.push({
      instance,
      depth,
      context: !selectedIds.has(instance.session_id),
    });
    for (const child of children.get(instance.session_id) ?? [])
      visit(child, depth + 1);
  }
  for (const root of roots) visit(root, 0);
  // Keep orphaned or malformed cyclic lineage visible rather than dropping instances.
  for (const instance of selected) visit(instance, 0);
  return rows;
}

export function InstanceLineage({
  selected,
  instances,
  disabled,
  onBurn,
}: {
  selected: Instance[];
  instances: Instance[];
  disabled: boolean;
  onBurn: (instance: Instance) => void;
}) {
  const byId = new Map(
    instances.map((instance) => [instance.session_id, instance]),
  );
  return (
    <table className="swarm-table">
      <thead>
        <tr>
          <th>Instance / lineage</th>
          <th>Status</th>
          <th>Last seen</th>
          <th>Exposures</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        {roleLineage(selected, instances).map(
          ({ instance, depth, context }) => {
            const parent = instance.parent_session
              ? byId.get(instance.parent_session)
              : undefined;
            return (
              <tr
                key={instance.session_id}
                className={context ? "swarm-context" : undefined}
              >
                <td
                  style={{ paddingLeft: `${12 + Math.min(depth, 6) * 18}px` }}
                >
                  <strong>
                    {context ? `Parent · ${instance.role}: ` : ""}
                    <a
                      className="instance-link"
                      href={instanceHref(instance.session_id)}
                      aria-label={`Inspect ${instance.agent_id}`}
                    >
                      {instance.agent_id}
                    </a>
                  </strong>
                  <code>{instance.session_id}</code>
                  {instance.parent_session && (
                    <span className="muted">
                      Child of {parent?.agent_id ?? instance.parent_session}
                      {parent ? ` (${parent.role})` : " · parent unavailable"}
                    </span>
                  )}
                  <ReplacementInfo instance={instance} instances={instances} />
                </td>
                <td>
                  <Status value={instance.status} />
                </td>
                <td>
                  {instance.last_seen ? (
                    <Timestamp value={instance.last_seen} />
                  ) : (
                    "Not reported"
                  )}
                </td>
                <td>{instance.exposures ?? "Not reported"}</td>
                <td>
                  {!context && (
                    <button
                      type="button"
                      className="burn-button"
                      disabled={disabled || instance.status !== "active"}
                      onClick={() => onBurn(instance)}
                      aria-label={`Burn instance ${instance.agent_id}`}
                    >
                      Burn instance
                    </button>
                  )}
                </td>
              </tr>
            );
          },
        )}
      </tbody>
    </table>
  );
}
