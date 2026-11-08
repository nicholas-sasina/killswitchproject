import { useId, type ReactNode } from "react";
import "./ui.css";

/**
 * The white card on the night stage, with its tracked uppercase label:
 * "RUNNING NOW / 19 AGENTS IN 4 ROLES". The label is the card's heading
 * (h2 by default) and names the section for assistive technology.
 */
export function Card({
  title,
  sub,
  aside,
  rule = true,
  level = 2,
  pad = "wide",
  className = "",
  children,
}: {
  /** The label, written in sentence case; CSS sets it in capitals. */
  title: ReactNode;
  /** After the slash, in the quiet colour. */
  sub?: ReactNode;
  /** Right of the label: a legend, a filter, a link. */
  aside?: ReactNode;
  /** A hairline under the header. */
  rule?: boolean;
  level?: 2 | 3;
  /** wide: the left column's 28px sides; narrow: the right column's 24px. */
  pad?: "wide" | "narrow";
  className?: string;
  children?: ReactNode;
}) {
  const id = useId();
  const Heading = level === 2 ? "h2" : "h3";
  return (
    <section className={`card card-${pad} ${className}`} aria-labelledby={id}>
      <div className={`card-head${rule ? " card-head-rule" : ""}`}>
        <Heading id={id} className="cap card-title">
          {title}
          {sub !== undefined && sub !== null && sub !== "" && (
            <span className="card-sub"> / {sub}</span>
          )}
        </Heading>
        {aside && <div className="card-aside">{aside}</div>}
      </div>
      {children}
    </section>
  );
}

/** The quiet line at the foot of a card that explains what it shows. */
export function CardNote({ children }: { children: ReactNode }) {
  return <p className="card-note">{children}</p>;
}
