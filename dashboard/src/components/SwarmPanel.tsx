import { useCallback, useMemo, useState } from "react";
import type { BurnApi } from "../api";
import type { Instance, Role, ScopedBurnResult } from "../contracts";
import { usePolling } from "../polling";
import { AddAgent } from "./AddAgent";
import { ErrorMessage } from "./common";
import { InstanceLineage } from "./InstanceLineage";
import { ScopedBurnDialog, type BurnTarget } from "./ScopedBurnDialog";
import "./swarm.css";

export function SwarmPanel({
  api,
  enabled,
  defaultStatus = "",
  sharedInstances,
}: {
  api: BurnApi;
  enabled: boolean;
  /**
   * The instance list from a poll the page already runs. When given, the
   * panel does not poll /instances itself: each call scans the sessions and
   * counts every instance's exposures, so one poll per page is enough.
   */
  sharedInstances?: { data?: { instances: Instance[] }; error: string };
  /**
   * Status the filter starts on. The overview passes "active" so contained
   * instances stay on the Contained view; the filter itself still offers
   * every status.
   */
  defaultStatus?: string;
}) {
  const loadRoles = useCallback(
    (signal: AbortSignal) => api.roles(signal),
    [api],
  );
  const loadInstances = useCallback(
    (signal: AbortSignal) => api.instances(undefined, signal),
    [api],
  );
  const rolePoll = usePolling(enabled ? loadRoles : null, 5_000);
  const ownPoll = usePolling(
    enabled && !sharedInstances ? loadInstances : null,
    5_000,
  );
  const instancePoll = sharedInstances ?? ownPoll;
  const [created, setCreated] = useState<Role[]>([]);
  const [revoked, setRevoked] = useState<string[]>([]);
  const [adding, setAdding] = useState<{ role?: Role }>();
  const [target, setTarget] = useState<BurnTarget>();
  const [outcome, setOutcome] = useState("");
  const [query, setQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [status, setStatus] = useState(defaultStatus);
  const roles = useMemo(
    () => [
      ...new Map(
        [...created, ...(rolePoll.data?.roles ?? [])].map((role) => [
          role.name,
          role,
        ]),
      ).values(),
    ],
    [created, rolePoll.data],
  );
  const instances = useMemo(
    () =>
      (instancePoll.data?.instances ?? []).map((instance): Instance =>
        revoked.includes(instance.session_id)
          ? { ...instance, status: "revoked" }
          : instance,
      ),
    [instancePoll.data, revoked],
  );
  const error = rolePoll.error || instancePoll.error;
  const loaded = Boolean(rolePoll.data && instancePoll.data);
  const search = query.trim().toLowerCase();
  function burned(result: ScopedBurnResult) {
    const sessions = result.burned.flatMap((entry) => entry.sessions_revoked);
    setRevoked((current) => [...new Set([...current, ...sessions])]);
    setOutcome(
      `${sessions.length} ${sessions.length === 1 ? "session" : "sessions"} revoked.${result.failed?.length ? ` ${result.failed.length} ${result.failed.length === 1 ? "burn" : "burns"} failed; check instance status before retrying.` : ""}`,
    );
    setTarget(undefined);
  }
  return (
    <section className="swarm-panel panel" aria-labelledby="swarm-title">
      <div className="swarm-heading">
        <div>
          <h2 id="swarm-title">Instances by role</h2>
          <p className="muted">
            {loaded
              ? `${roles.length} roles · ${instances.length} instances · refreshes every five seconds`
              : "Waiting for role and instance data"}
          </p>
        </div>
        <button
          className="secondary-button"
          type="button"
          disabled={!enabled || !loaded || Boolean(error)}
          onClick={() => setAdding({})}
        >
          Add agent
        </button>
      </div>
      <ErrorMessage
        message={
          error
            ? `${error} Retaining the last received roles and instances.`
            : ""
        }
      />
      <div className="swarm-filters">
        <label>
          Find instance
          <input
            type="search"
            value={query}
            placeholder="Agent, session or owner"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <label>
          Role
          <select
            value={roleFilter}
            onChange={(event) => setRoleFilter(event.target.value)}
          >
            <option value="">All roles</option>
            {roles.map((role) => (
              <option key={role.name}>{role.name}</option>
            ))}
          </select>
        </label>
        <label>
          Status
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="">All statuses</option>
            {["active", "revoked", "exited"].map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
      </div>
      {outcome && (
        <p role="status" aria-label="Scoped burn result">
          {outcome}
        </p>
      )}
      <div
        className="swarm-viewport"
        tabIndex={0}
        role="region"
        aria-label="Role instances"
      >
        {roles
          .filter((role) => !roleFilter || role.name === roleFilter)
          .map((role) => {
            const all = instances.filter(
              (instance) => instance.role === role.name,
            );
            const selected = all.filter(
              (instance) =>
                (!status || instance.status === status) &&
                (!search ||
                  [
                    instance.agent_id,
                    instance.session_id,
                    instance.owner ?? "",
                  ].some((value) => value.toLowerCase().includes(search))),
            );
            return (
              <section
                className="swarm-role"
                key={role.name}
                aria-label={`Role ${role.name}`}
              >
                <div className="swarm-heading">
                  <div>
                    <h3>
                      {role.name}{" "}
                      {role.auto_burn === false && (
                        <span className="status">report-only</span>
                      )}{" "}
                      {role.canaries === false && (
                        <span className="status">no canary</span>
                      )}
                    </h3>
                    <p className="muted">
                      {role.managed_by === "code"
                        ? "Code-managed · read-only"
                        : role.managed_by === "scenario"
                          ? "Scenario"
                          : "Dashboard-managed"}
                      {role.owner ? ` · ${role.owner}` : ""} ·{" "}
                      {
                        all.filter((instance) => instance.status === "active")
                          .length
                      }{" "}
                      active / {all.length} instances
                    </p>
                  </div>
                  <div className="swarm-actions">
                    {role.managed_by === "ui" && (
                      <button
                        className="secondary-button"
                        type="button"
                        disabled={Boolean(error) || !loaded}
                        onClick={() => setAdding({ role })}
                        aria-label={`New token for ${role.name}`}
                      >
                        New token
                      </button>
                    )}
                    <button
                      className="burn-button"
                      type="button"
                      disabled={
                        Boolean(error) ||
                        !loaded ||
                        !all.some((instance) => instance.status === "active")
                      }
                      onClick={() =>
                        setTarget({
                          scope: "role",
                          id: role.name,
                          label: role.name,
                        })
                      }
                      aria-label={`Burn role ${role.name}`}
                    >
                      Burn role
                    </button>
                  </div>
                </div>
                {selected.length ? (
                  <InstanceLineage
                    selected={selected}
                    instances={instances}
                    disabled={Boolean(error) || !loaded}
                    onBurn={(instance) =>
                      setTarget({
                        scope: "instance",
                        id: instance.session_id,
                        label: instance.agent_id,
                      })
                    }
                  />
                ) : (
                  <p className="swarm-empty">
                    {all.length
                      ? "No instances match these filters."
                      : "No instances yet."}
                  </p>
                )}
              </section>
            );
          })}
        {loaded && !roles.length && (
          <p className="swarm-empty">
            No roles yet. Add an agent to create a role and join token.
          </p>
        )}
      </div>
      {adding && (
        <AddAgent
          api={api}
          roles={roles}
          instances={instances}
          role={adding.role}
          onCreated={(role) => setCreated((current) => [...current, role])}
          onClose={() => setAdding(undefined)}
        />
      )}
      {target && (
        <ScopedBurnDialog
          api={api}
          target={target}
          onClose={() => setTarget(undefined)}
          onBurned={burned}
        />
      )}
    </section>
  );
}
