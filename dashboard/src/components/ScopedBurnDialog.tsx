import { useState } from "react";
import type { BurnApi } from "../api";
import type { ScopedBurnRequest, ScopedBurnResult } from "../contracts";
import { ErrorMessage } from "./common";
import { SwarmDialog } from "./SwarmDialog";

export interface BurnTarget {
  scope: "instance" | "role";
  id: string;
  label: string;
}

export function ScopedBurnDialog({ api, target, onClose, onBurned }: {
  api: BurnApi;
  target: BurnTarget;
  onClose: () => void;
  onBurned: (result: ScopedBurnResult) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reason, setReason] = useState("");
  async function confirm() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const body: ScopedBurnRequest = { scope: target.scope, id: target.id };
      if (reason.trim()) body.reason = reason.trim();
      onBurned(await api.burnScope(body));
    } catch (error) {
      setError(`${error instanceof Error ? error.message : "Burn failed."} Outcome not confirmed. Check instance status before retrying.`);
    } finally {
      setBusy(false);
    }
  }
  return <SwarmDialog title={`Burn ${target.scope} ${target.label}?`} busy={busy} onClose={onClose}>
    <form onSubmit={(event) => { event.preventDefault(); void confirm(); }}>
      <p>{target.scope === "role" ? "Revoke every active instance of this role." : "Revoke this instance's session."} Rotate its own credentials and flag other credentials it saw for review.</p>
      <label>Reason (optional)<input value={reason} maxLength={200} disabled={busy} onChange={(event) => setReason(event.target.value)} /></label>
      <ErrorMessage message={error} />
      {busy && <p role="status">Containing… Keep this window open for the result.</p>}
      <div className="dialog-actions">
        <button type="button" className="secondary-button" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" className="burn-button" disabled={busy}>{busy ? "Containing…" : "Confirm Burn"}</button>
      </div>
    </form>
  </SwarmDialog>;
}
