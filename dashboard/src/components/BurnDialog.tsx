import { useEffect, useRef, useState } from "react";
import type { AgentState } from "../contracts";
import { BurnMark, ErrorMessage } from "./common";

export function BurnDialog({
  agent,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  agent: AgentState;
  busy: boolean;
  error: string;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const [reason, setReason] = useState("");
  useEffect(() => {
    dialog.current?.showModal();
    cancelButton.current?.focus();
  }, []);
  return (
    <dialog
      ref={dialog}
      aria-labelledby="burn-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onCancel();
      }}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!busy) onConfirm(reason);
        }}
      >
        <div className="dialog-icon">
          <BurnMark />
        </div>
        <p className="eyebrow">Contain this agent</p>
        <h2 id="burn-title">Burn {agent.agent_id}?</h2>
        <p>
          This revokes its sessions, rotates its own credentials, and flags
          credentials it saw that belong to others for review.
        </p>
        <label htmlFor="burn-reason">
          Reason <span className="muted">(optional)</span>
        </label>
        <input
          id="burn-reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          maxLength={200}
          disabled={busy}
          autoComplete="off"
          placeholder="Why is this agent being contained?"
        />
        <ErrorMessage message={error} />
        {busy && (
          <p role="status">
            Containing agent… Keep this window open for the result.
          </p>
        )}
        <div className="dialog-actions">
          <button
            ref={cancelButton}
            type="button"
            className="secondary-button"
            onClick={onCancel}
            disabled={busy}
            autoFocus
          >
            Cancel
          </button>
          <button className="burn-button" type="submit" disabled={busy}>
            <BurnMark />
            {busy ? "Containing…" : "Confirm Burn"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
