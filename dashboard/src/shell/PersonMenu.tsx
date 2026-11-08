import { useEffect, useId, useRef, useState } from "react";
import { initials } from "../model/people";

/**
 * The signed-in person, and Sign out. Opens a small menu below the badge;
 * Escape or a click elsewhere closes it.
 */
export function PersonMenu({
  email,
  logoutUrl,
  onSignOut,
}: {
  /** Undefined in local development, where nobody signs in. */
  email?: string;
  logoutUrl?: string;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const menu = useId();
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const who = email ?? "Local development";
  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [open]);
  return (
    <div
      ref={root}
      className="person"
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          setOpen(false);
          button.current?.focus();
        }
      }}
    >
      <button
        ref={button}
        type="button"
        className="person-badge"
        aria-expanded={open}
        aria-controls={menu}
        aria-label={`Signed in as ${who}`}
        title={who}
        onClick={() => setOpen((value) => !value)}
      >
        {email ? initials(email) : "LD"}
      </button>
      <div id={menu} className="person-menu" hidden={!open}>
        <p className="person-who">
          <span className="person-label">Signed in as</span>
          <span className="person-email">{who}</span>
        </p>
        <a
          className="person-signout"
          href={logoutUrl ?? "#/overview"}
          onClick={(event) => {
            event.preventDefault();
            onSignOut();
          }}
        >
          Sign out
        </a>
      </div>
    </div>
  );
}
