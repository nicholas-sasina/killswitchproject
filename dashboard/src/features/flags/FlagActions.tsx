/**
 * An owner's decision on one flagged key: Rotate it, or Dismiss the flag
 * with a reason (POST /flags/resolve). Both open a dialog first, because
 * the API needs a reason for either one (it goes in the event feed), and
 * because rotating a shared key stops anything outside Burn that still holds
 * the old value.
 *
 * On success the flag's new state goes into the console's list at once
 * (noteFlag), the flags poll runs now, and the parent hears about it.
 */
import { useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ApiError } from "../../api";
import type { Flag, FlagResolveResult } from "../../contracts";
import { useConsole } from "../../data/ConsoleData";
import { Dialog, Notice } from "../../ui";
import { announce } from "./announce";
import {
  decided,
  flagKey,
  REASON_MAX,
  reasonProblem,
  reopenNote,
  resolveError,
  safeBy,
} from "./describe";
import "./flags.css";

export interface FlagActionsProps {
  flag: Flag;
  /** compact: "Rotate" and "Dismiss" (Overview); full: "Dismiss with a reason". */
  variant?: "compact" | "full";
  /**
   * Called after the API answered. FlagActions has already put the flag's
   * new state in the console's list with noteFlag, so counts update at once.
   */
  onResolved?: (flag: Flag, result: FlagResolveResult) => void;
}

type Action = "rotate" | "dismiss";

/**
 * When the control that had focus is gone, put focus on the card's heading.
 * The dialog returns focus to its opener when it closes, so this waits for
 * that (and for the page to stop being inert) before deciding.
 */
function keepFocusIn(section: Element | null | undefined, tries = 10) {
  window.setTimeout(() => {
    const active = document.activeElement;
    if (active && active !== document.body && active.isConnected) return;
    const heading = section?.querySelector<HTMLElement>("h2, h3");
    if (!heading?.isConnected) return;
    if (heading.closest("[inert]")) {
      if (tries > 0) keepFocusIn(section, tries - 1);
      return;
    }
    heading.tabIndex = -1;
    heading.focus();
  }, 16);
}

export function FlagActions({
  flag,
  variant = "full",
  onResolved,
}: FlagActionsProps) {
  const { noteFlag, refreshFlags } = useConsole();
  const [action, setAction] = useState<Action>();
  const root = useRef<HTMLDivElement>(null);
  const key = flagKey(flag);
  const open = flag.state === "open";
  const dismissLabel =
    variant === "compact" ? "Dismiss" : "Dismiss with a reason";

  function resolved(next: Flag, result: FlagResolveResult, done: Action) {
    // The row holding these buttons usually goes once the flag is resolved,
    // and focus would go with it. Keep it in the card instead.
    const section = root.current?.closest("section");
    setAction(undefined);
    noteFlag(next);
    refreshFlags();
    onResolved?.(next, result);
    announce(
      done === "rotate"
        ? `${key} rotated. Agents read the new value through references.`
        : `Flag on ${key} dismissed.`,
    );
    keepFocusIn(section);
  }

  return (
    <div className="flag-actions" ref={root}>
      <button
        type="button"
        className="btn btn-dark btn-sm"
        aria-haspopup="dialog"
        aria-label={`Rotate ${key}`}
        disabled={!open}
        onClick={() => setAction("rotate")}
      >
        Rotate
      </button>
      <button
        type="button"
        className="btn btn-sm"
        aria-haspopup="dialog"
        aria-label={`${dismissLabel} for ${key}`}
        disabled={!open}
        onClick={() => setAction("dismiss")}
      >
        {dismissLabel}
      </button>
      {action && (
        <ResolveDialog
          flag={flag}
          action={action}
          onClose={() => setAction(undefined)}
          onResolved={(next, result) => resolved(next, result, action)}
        />
      )}
    </div>
  );
}

function ResolveDialog({
  flag,
  action,
  onClose,
  onResolved,
}: {
  flag: Flag;
  action: Action;
  onClose: () => void;
  onResolved: (flag: Flag, result: FlagResolveResult) => void;
}) {
  const { api, email, refreshFlags } = useConsole();
  const ids = {
    title: useId(),
    body: useId(),
    field: useId(),
    hint: useId(),
    problem: useId(),
  };
  const field = useRef<HTMLTextAreaElement>(null);
  const running = useRef(false);
  const [reason, setReason] = useState("");
  const [problem, setProblem] = useState<string>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const key = flagKey(flag);
  const rotate = action === "rotate";

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (running.current) return;
    const text = reason.trim();
    const wrong = reasonProblem(text);
    setProblem(wrong);
    if (wrong) {
      field.current?.focus();
      return;
    }
    running.current = true;
    setBusy(true);
    setError(undefined);
    const by = safeBy(email);
    try {
      const result = await api.resolveFlag({
        secret_ref: flag.secret_ref,
        action,
        reason: text,
        ...(by ? { by } : {}),
      });
      onResolved(
        result.flag ??
          decided(flag, action, text, by, new Date().toISOString()),
        result,
      );
    } catch (failure) {
      setError(resolveError(failure, key));
      if (failure instanceof ApiError && failure.status === 404) refreshFlags();
      running.current = false;
      setBusy(false);
    }
  }

  const effects: ReactNode[] = rotate
    ? [
        "Burn mints a new value now and clears the flag.",
        "Agents read keys through references, so running agents pick up the new value.",
        "Anything outside Burn that still holds the old value stops working until it is updated.",
      ]
    : [
        <>
          <span className="id">{key}</span> keeps its current value, and the
          flag closes.
        </>,
        reopenNote(flag.flag_level),
      ];

  return (
    <Dialog
      labelledBy={ids.title}
      describedBy={ids.body}
      onClose={onClose}
      busy={busy}
      initialFocus={field}
      className="flag-dialog"
    >
      <form onSubmit={submit} noValidate>
        <p className="cap dialog-eyebrow">
          {rotate ? "Rotate a flagged key" : "Dismiss a flag"}
        </p>
        <h2 id={ids.title} className="dialog-title">
          {rotate ? "Rotate " : "Dismiss the flag on "}
          <span className="id">{key}</span>?
        </h2>
        <ul id={ids.body} className="dialog-body flag-dialog-effects">
          {effects.map((effect, index) => (
            <li key={index}>{effect}</li>
          ))}
        </ul>
        <div className="flag-reason">
          <label htmlFor={ids.field}>Reason</label>
          <p id={ids.hint} className="flag-reason-hint">
            Shown in the event feed. Never paste a key&rsquo;s value.
          </p>
          <textarea
            id={ids.field}
            ref={field}
            rows={3}
            maxLength={REASON_MAX}
            value={reason}
            readOnly={busy}
            aria-invalid={problem ? true : undefined}
            aria-describedby={problem ? `${ids.hint} ${ids.problem}` : ids.hint}
            onChange={(event) => {
              setReason(event.target.value);
              if (problem) setProblem(reasonProblem(event.target.value));
            }}
          />
          <div className="flag-reason-foot">
            {problem && (
              <p id={ids.problem} className="flag-reason-problem">
                {problem}
              </p>
            )}
            <span className="flag-reason-count num" aria-hidden="true">
              {reason.trim().length}/{REASON_MAX}
            </span>
          </div>
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        <div className="dialog-actions">
          <button
            type="submit"
            className="btn btn-dark"
            aria-disabled={busy || undefined}
          >
            {busy
              ? rotate
                ? "Rotating…"
                : "Dismissing…"
              : rotate
                ? `Rotate ${key}`
                : "Dismiss the flag"}
          </button>
          <button
            type="button"
            className="btn"
            aria-disabled={busy || undefined}
            onClick={() => {
              if (!busy) onClose();
            }}
          >
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}
