/**
 * One header per view: what this page is, in a sentence, with the figures
 * that matter on the right.
 *
 * Replaces the old pattern where every view opened straight into a dense
 * ruled panel. A page with no entry point reads as a schematic, and the
 * figures at display scale give the layout the scale contrast it plays
 * against.
 */
import type { ReactNode } from "react";

export interface Figure {
  label: string;
  value: number | string;
  tone?: "nominal" | "ember" | "caution";
}

export function PageHead({
  title,
  description,
  figures = [],
  meta,
  attention = false,
  children,
}: {
  title: string;
  description: string;
  figures?: Figure[];
  meta?: string;
  /** Marks the header when something is revoked or queued. */
  attention?: boolean;
  children?: ReactNode;
}) {
  return (
    <header className="page-head" data-attention={attention}>
      <div className="page-head-text">
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {figures.length > 0 && (
        <dl className="page-figures">
          {figures.map((figure) => (
            <div key={figure.label} data-tone={figure.tone}>
              {/* Term before definition, as a dl requires; CSS lifts the
                  figure above its label. */}
              <dt>{figure.label}</dt>
              <dd>{figure.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {(meta || children) && (
        <div className="page-head-aside">
          {children}
          {meta && <p className="page-meta">{meta}</p>}
        </div>
      )}
    </header>
  );
}
