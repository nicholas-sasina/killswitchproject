// STUB: the shared interface only. The incidents page's branch implements it
// (a reason in a Dialog for both actions, POST /flags/resolve, plain errors).
import type { Flag, FlagResolveResult } from "../../contracts";

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

export function FlagActions({ variant = "full" }: FlagActionsProps) {
  return (
    <div className="flag-actions">
      <button className="btn btn-dark btn-sm" type="button" disabled>
        Rotate
      </button>
      <button className="btn btn-sm" type="button" disabled>
        {variant === "compact" ? "Dismiss" : "Dismiss with a reason"}
      </button>
    </div>
  );
}
