import { memo, useMemo } from "react";
import { useConsole } from "../../data/ConsoleData";
import type { ConsoleAgent, Counts } from "../../model";
import { dayTime, isoTime } from "../../model";
import { agentHref } from "../../router";
import {
  Card,
  CardNote,
  EmptyState,
  Notice,
  Segmented,
  Square,
  type SegmentOption,
} from "../../ui";
import {
  keysText,
  lastCall,
  lastCallText,
  lastCalls,
  rolePlural,
  roleRows,
  startedBy,
  type AgentRow,
  type Filter,
  type KeysSeen,
} from "./model";
import { useKeysSeen } from "./useKeysSeen";

const LABELS: Record<Filter, string> = {
  running: "Running",
  burned: "Burned",
  finished: "Finished",
  all: "All",
};

/** Indent per level of lineage, up to three levels. */
const INDENT = 22;

const Row = memo(function Row({
  row,
  by,
  last,
  keys,
  now,
  onBurn,
}: {
  row: AgentRow;
  by: string;
  last: string | undefined;
  keys: KeysSeen | undefined;
  now: Date;
  onBurn: (agent: ConsoleAgent) => void;
}) {
  const { agent, depth } = row;
  const called = lastCallText(agent.state, last, now);
  const saw = keysText(keys, agent.exposures, agent.state);
  return (
    <tr className={`role-row is-${agent.state}`}>
      <td>
        <div
          className="role-agent"
          style={{ paddingLeft: Math.min(depth, 3) * INDENT }}
        >
          <Square
            state={agent.state}
            label={`${agent.name}, ${agent.state}`}
            pulseKey={agent.key}
            size={12}
          />
          <span className="role-agent-text">
            <a className="role-agent-link id" href={agentHref(agent.key)}>
              {agent.name}
            </a>
            {by && <span className="table-sub">{by}</span>}
          </span>
        </div>
      </td>
      <td className="role-started num">
        {agent.createdAt ? (
          <time dateTime={isoTime(agent.createdAt)}>
            {dayTime(agent.createdAt, now)}
          </time>
        ) : (
          <span className="role-quiet">Not reported</span>
        )}
      </td>
      <td className={`num${called.quiet ? " role-quiet" : ""}`}>
        {last ? (
          <time dateTime={isoTime(last)}>{called.text}</time>
        ) : (
          called.text
        )}
      </td>
      <td
        className={
          saw.hot ? "role-hot" : saw.quiet ? "role-quiet" : "role-keys"
        }
      >
        {saw.text}
      </td>
      <td className="is-end">
        {agent.state === "running" ? (
          <button
            className="btn btn-sm"
            type="button"
            aria-label={`Burn ${agent.name}`}
            onClick={() => onBurn(agent)}
          >
            Burn
          </button>
        ) : agent.state === "burned" ? (
          <a
            className="role-incident"
            href={agentHref(agent.key)}
            aria-label={`Incident: ${agent.name}`}
          >
            Incident
          </a>
        ) : null}
      </td>
    </tr>
  );
});

/**
 * The role's agents: one row per agent, filtered by state, children under
 * their parents. Counts are the role's own (`counts`), from the console's
 * one model.
 */
export function AgentsCard({
  role,
  counts,
  filter,
  onFilter,
  canAdd,
  onBurn,
}: {
  role: string;
  counts: Counts;
  filter: Filter;
  onFilter: (filter: Filter) => void;
  /** Whether the role takes join tokens (not a scenario role). */
  canAdd: boolean;
  onBurn: (agent: ConsoleAgent) => void;
}) {
  const { api, model, events, instancesLoaded, instancesError } = useConsole();
  const plural = rolePlural(role);
  const rows = useMemo(
    () => roleRows(model, role, filter),
    [model, role, filter],
  );
  const calls = useMemo(() => lastCalls(events), [events]);
  const wanted = useMemo(
    () =>
      rows.map((row) => ({
        agentId: row.agent.agentId,
        exposures: row.agent.exposures,
      })),
    [rows],
  );
  const seen = useKeysSeen(api, wanted);
  const options: SegmentOption<Filter>[] = [
    { value: "running", label: LABELS.running, count: counts.running },
    { value: "burned", label: LABELS.burned, count: counts.burned },
    { value: "finished", label: LABELS.finished, count: counts.finished },
    { value: "all", label: LABELS.all, count: counts.agents },
  ];
  const add = canAdd ? ` Add ${plural} to start one.` : "";
  const empty: Record<Filter, string> = {
    running: `No ${plural} running.${add}`,
    burned: `No ${role} has been burned.`,
    finished: `No ${role} has finished yet. Finished ${plural} keep their history here.`,
    all: `No ${plural} yet.${add}`,
  };
  return (
    <Card
      title={plural}
      sub="each one a running copy"
      aside={
        <Segmented
          label={`Show ${plural}`}
          options={options}
          value={filter}
          onChange={onFilter}
        />
      }
      className="role-agents"
    >
      {!instancesLoaded ? (
        instancesError ? (
          <Notice tone="error">
            {`${plural[0].toUpperCase()}${plural.slice(1)} could not be loaded: ${instancesError}`}
          </Notice>
        ) : (
          <EmptyState>Loading {plural}…</EmptyState>
        )
      ) : rows.length ? (
        <table className="table role-table">
          <colgroup>
            <col />
            <col className="role-col-started" />
            <col className="role-col-last" />
            <col className="role-col-keys" />
            <col className="role-col-action" />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">Agent</th>
              <th scope="col" className="role-started">
                Started
              </th>
              <th scope="col">Last call</th>
              <th scope="col">Keys it saw</th>
              <th scope="col">
                <span className="visually-hidden">Action</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <Row
                key={row.agent.key}
                row={row}
                by={startedBy(row, model)}
                last={lastCall(row.agent, calls)}
                keys={seen.get(row.agent.agentId)}
                now={model.now}
                onBurn={onBurn}
              />
            ))}
          </tbody>
        </table>
      ) : (
        <EmptyState>{empty[filter]}</EmptyState>
      )}
      <CardNote>
        Indented agents were spawned by the one above; they can never have more
        tools than their parent. Finished agents keep their history; their own
        keys were deleted when they exited.
      </CardNote>
    </Card>
  );
}
