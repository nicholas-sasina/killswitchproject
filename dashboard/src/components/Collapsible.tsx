/**
 * A panel that collapses to its heading.
 *
 * The body stays mounted when closed so a collapsed module keeps whatever
 * state it had and reopens instantly. Whether a closed module keeps polling
 * is the caller's decision, because the two modules differ:
 *
 *   - The exposure ledger keeps polling while closed, since its heading
 *     summarises what it found and that summary has to stay true.
 *   - The CloudTrail timeline stops, since a closed timeline says nothing
 *     and its query is the expensive one.
 */
import { useEffect, useId, useState, type ReactNode } from "react";

export function Collapsible({
  label,
  summary,
  defaultOpen = false,
  /** When this becomes true the module opens itself once. */
  openWhen = false,
  /** Lets a caller stop work while the module is closed. */
  onOpenChange,
  className = "",
  children,
}: {
  label: string;
  summary?: ReactNode;
  defaultOpen?: boolean;
  openWhen?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen || openWhen);
  const bodyId = useId();
  // Opening is automatic; closing stays the operator's choice, so this only
  // ever opens and never forces a close.
  useEffect(() => {
    if (openWhen) setOpen(true);
  }, [openWhen]);
  useEffect(() => {
    onOpenChange?.(open);
  }, [open, onOpenChange]);
  return (
    <section className={`panel ${className}`.trim()}>
      <button
        type="button"
        className="module-toggle"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="chevron" aria-hidden="true">
          {open ? "–" : "+"}
        </span>
        <h2>{label}</h2>
        {summary && <span className="toggle-meta">{summary}</span>}
      </button>
      <div id={bodyId} className="module-body" hidden={!open}>
        {children}
      </div>
    </section>
  );
}
