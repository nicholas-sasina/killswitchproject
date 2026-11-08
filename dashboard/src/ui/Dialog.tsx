import {
  useEffect,
  useRef,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import "./ui.css";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * A white dialog on the dimmed night stage. It traps focus, closes on
 * Escape (unless `busy`), returns focus to whatever opened it, and opens
 * with a 160 ms fade and a 6 px rise (none with reduced motion). The rest
 * of the page is inert while it is open.
 *
 * Render it only while open; unmounting it closes it.
 */
export function Dialog({
  labelledBy,
  describedBy,
  onClose,
  busy = false,
  initialFocus,
  width = 520,
  className = "",
  children,
}: {
  /** The id of the dialog's heading. */
  labelledBy: string;
  describedBy?: string;
  onClose: () => void;
  /** While a request runs: Escape and the scrim do nothing. */
  busy?: boolean;
  /** What takes focus first; otherwise the first control. */
  initialFocus?: RefObject<HTMLElement | null>;
  width?: number;
  className?: string;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const opener =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const root = document.getElementById("root");
    const wasInert = root?.inert ?? false;
    if (root && !root.contains(panel.current)) root.inert = true;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const first =
      initialFocus?.current ??
      panel.current?.querySelector<HTMLElement>(FOCUSABLE) ??
      panel.current;
    first?.focus();
    return () => {
      if (root) root.inert = wasInert;
      document.body.style.overflow = overflow;
      if (opener?.isConnected) opener.focus();
    };
    // Focus moves once, when the dialog opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.stopPropagation();
      if (!busy) close.current();
      return;
    }
    if (event.key !== "Tab" || !panel.current) return;
    const items = [
      ...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE),
    ].filter((item) => !item.closest("[hidden], [inert]"));
    const list = items.length ? items : [panel.current];
    const [head, tail] = [list[0], list[list.length - 1]];
    if (event.shiftKey && document.activeElement === head) {
      event.preventDefault();
      tail.focus();
    } else if (!event.shiftKey && document.activeElement === tail) {
      event.preventDefault();
      head.focus();
    }
  }

  return createPortal(
    <div
      className="dialog-scrim"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) close.current();
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        aria-busy={busy || undefined}
        tabIndex={-1}
        className={`dialog card ${className}`}
        style={{ width: `min(${width}px, 100%)` }}
        onKeyDown={onKeyDown}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
