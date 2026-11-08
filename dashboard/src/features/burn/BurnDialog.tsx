// STUB: the shared interface only. The agent page's branch implements it
// (the confirmation list from real data, the request, plain errors).
import { useId } from "react";
import { Dialog } from "../../ui";
import type { BurnDialogProps } from "./types";

export type { BurnDialogProps, BurnOutcome, BurnTarget } from "./types";

export function BurnDialog({ target, onClose }: BurnDialogProps) {
  const title = useId();
  const name =
    target.scope === "agent" ? target.agent.name : `every ${target.role}`;
  return (
    <Dialog labelledBy={title} onClose={onClose}>
      <p className="cap dialog-eyebrow">
        {target.scope === "agent" ? "Burn one agent" : "Burn a role"}
      </p>
      <h2 id={title} className="dialog-title">
        Burn {name}?
      </h2>
      <div className="dialog-actions">
        <button className="btn btn-ember" type="button" disabled>
          Burn {name}
        </button>
        <button className="btn" type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
    </Dialog>
  );
}
