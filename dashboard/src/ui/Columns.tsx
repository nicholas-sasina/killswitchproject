import type { ReactNode } from "react";
import "./ui.css";

/**
 * The page grid under a hero: the main column and the right column, which
 * stacks under the main one below 1100px.
 */
export function Columns({
  aside,
  asideWidth = 400,
  children,
}: {
  aside?: ReactNode;
  /** 420 on the Overview, 400 elsewhere. */
  asideWidth?: 400 | 420;
  children: ReactNode;
}) {
  return (
    <div className={`columns wrap${aside ? ` columns-${asideWidth}` : ""}`}>
      <div className="column">{children}</div>
      {aside && <div className="column">{aside}</div>}
    </div>
  );
}
