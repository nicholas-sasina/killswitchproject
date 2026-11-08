import type { ReactNode } from "react";
import "./ui.css";

/**
 * What an empty card says: what will appear here, and how to make it
 * appear. "No agents running. Connect an agent to start one."
 */
export function EmptyState({
  children,
  action,
}: {
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <p>{children}</p>
      {action && <div className="empty-state-action">{action}</div>}
    </div>
  );
}

/**
 * A plain sentence about something that went wrong, or is still loading,
 * inside a card or on the night stage. Errors are announced.
 */
export function Notice({
  tone = "info",
  children,
}: {
  tone?: "info" | "error";
  children: ReactNode;
}) {
  return (
    <p
      className={`notice notice-${tone}`}
      role={tone === "error" ? "alert" : "status"}
    >
      {children}
    </p>
  );
}

export interface Fact {
  label: ReactNode;
  value: ReactNode;
  /** A second line under the label, in the quiet colour. */
  note?: ReactNode;
  /** Set the value in Geist Mono: ids, keys, sessions. */
  mono?: boolean;
}

/**
 * Label and value rows, as in Identity, Protections and "How burns
 * started". Rows with no value are left out rather than showing a dash.
 */
export function Facts({ rows }: { rows: Fact[] }) {
  const shown = rows.filter(
    (row) => row.value !== undefined && row.value !== null && row.value !== "",
  );
  return (
    <dl className="facts">
      {shown.map((row, index) => (
        <div key={index} className="facts-row">
          <dt>
            <span className="facts-label">{row.label}</span>
            {row.note && <span className="facts-note">{row.note}</span>}
          </dt>
          <dd className={row.mono ? "id" : undefined}>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export interface Figure {
  label: string;
  value: ReactNode;
  /** Ember ink, for evidence such as canary uses above zero. */
  tone?: "ember";
}

/**
 * The quiet stat strip inside a card: "KEYS SEEN 4 | IN PLAIN TEXT 1 |
 * OWNED 2" (row), or the Overview's "Last 24 hours" grid (grid). Only the
 * strips the console has; no new stat bands.
 */
export function Figures({
  items,
  layout = "row",
  columns = 3,
}: {
  items: Figure[];
  layout?: "row" | "grid";
  columns?: number;
}) {
  return (
    <dl
      className={`figures figures-${layout}`}
      style={
        layout === "grid"
          ? { gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }
          : undefined
      }
    >
      {items.map((item) => (
        <div key={item.label} className="figure">
          <dt className="cap">{item.label}</dt>
          <dd className={`num${item.tone === "ember" ? " is-ember" : ""}`}>
            {item.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** A mono chip, for a tool or host name. */
export function Chip({ children }: { children: ReactNode }) {
  return <span className="chip">{children}</span>;
}
