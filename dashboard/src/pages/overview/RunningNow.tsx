import { memo, useCallback, useRef, useState, type KeyboardEvent } from "react";
import { useConsole } from "../../data/ConsoleData";
import { Card, CardNote, EmptyState, Notice, Square } from "../../ui";
import { sameRow, type NowRow } from "./overview";

const KEYS: Record<string, (index: number, last: number) => number> = {
  ArrowRight: (i, last) => Math.min(i + 1, last),
  ArrowDown: (i, last) => Math.min(i + 1, last),
  ArrowLeft: (i) => Math.max(i - 1, 0),
  ArrowUp: (i) => Math.max(i - 1, 0),
  Home: () => 0,
  End: (_, last) => last,
};

/**
 * One role: its name and line, a square per agent, and its counts. The
 * whole row opens the role; each square opens its agent. The squares are a
 * single tab stop, moved through with the arrow keys, so a role of 200
 * agents is one stop and not 200.
 *
 * Re-renders only when something on the row changed (see `sameRow`): a poll
 * that brings new events but no new agents leaves every row alone, and the
 * squares dim themselves from the pulse bus.
 */
const RoleRow = memo(
  function RoleRow({ row }: { row: NowRow }) {
    const [current, setCurrent] = useState(0);
    const elements = useRef(new Map<string, HTMLElement>());
    const setters = useRef(new Map<string, (el: HTMLElement | null) => void>());
    // One stable ref callback per square, so a memoised Square stays put.
    const refFor = useCallback((key: string) => {
      let setter = setters.current.get(key);
      if (!setter) {
        setter = (element) => {
          if (element) elements.current.set(key, element);
          else elements.current.delete(key);
        };
        setters.current.set(key, setter);
      }
      return setter;
    }, []);
    const active = Math.min(current, row.squares.length - 1);
    const onKeyDown = (event: KeyboardEvent) => {
      const move = KEYS[event.key];
      if (!move) return;
      event.preventDefault();
      const next = move(active, row.squares.length - 1);
      setCurrent(next);
      elements.current.get(row.squares[next].key)?.focus();
    };
    const burned = row.burnedToday;
    return (
      <li className="ov-row">
        <div className="ov-row-role">
          <a className="ov-row-name" href={row.href}>
            {row.name}
          </a>
          {row.what && <span className="ov-row-what">{row.what}</span>}
        </div>
        <div
          className="ov-squares"
          role="group"
          aria-label={`${row.name} agents: ${row.running} running, ${burned} burned today`}
          aria-describedby="ov-squares-keys"
          onKeyDown={onKeyDown}
        >
          {row.squares.map((square, index) => (
            <Square
              key={square.key}
              ref={refFor(square.key)}
              state={square.state}
              label={square.label}
              href={square.href}
              pulseKey={square.key}
              tabIndex={index === active ? 0 : -1}
            />
          ))}
        </div>
        <div className="ov-row-counts">
          <span className="ov-row-running num">{row.running} running</span>
          <span className={`ov-row-note num${burned ? " is-ember" : ""}`}>
            {burned ? `${burned} burned today` : "Nothing burned today"}
          </span>
        </div>
      </li>
    );
  },
  (prev, next) => sameRow(prev.row, next.row),
);

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** "Running now": a row per role, a square per agent. */
export function RunningNow({
  rows,
  loading,
}: {
  rows: NowRow[];
  loading: boolean;
}) {
  const { model } = useConsole();
  const { running, rolesNow } = model.counts;
  return (
    <Card
      title="Running now"
      sub={
        loading
          ? undefined
          : `${plural(running, "agent")} in ${plural(rolesNow, "role")}`
      }
      className="ov-now"
      aside={
        <ul className="ov-legend" aria-label="Legend">
          <li className="ov-legend-item">
            <span
              className="square square-running square-12"
              aria-hidden="true"
            />
            Running
          </li>
          <li className="ov-legend-item">
            <span
              className="square square-burned square-12"
              aria-hidden="true"
            />
            Burned today, still shown here for one day
          </li>
        </ul>
      }
    >
      {loading ? (
        <Notice>Loading agents.</Notice>
      ) : rows.length === 0 ? (
        <EmptyState>No agents running. Add agents to start a role.</EmptyState>
      ) : (
        <>
          <ul className="ov-rows">
            {rows.map((row) => (
              <RoleRow key={row.name} row={row} />
            ))}
          </ul>
          <p id="ov-squares-keys" className="visually-hidden">
            Use the arrow keys to move between a role’s agents.
          </p>
          <CardNote>
            Each row is a role; each square is one agent of that role. Finished
            agents leave this view. Select a role to see all its agents,
            including finished ones.
          </CardNote>
        </>
      )}
    </Card>
  );
}
