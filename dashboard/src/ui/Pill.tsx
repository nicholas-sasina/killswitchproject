import type { ReactNode } from "react";
import type { AgentState } from "../model/console";
import "./ui.css";

/**
 * A small label on white. neutral: tracked or routine; flag: waiting on an
 * owner (ember tint); burn: burning (ember fill); done: finished by Burn,
 * such as Rotated or Revoked (ink fill).
 */
export function Pill({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "flag" | "burn" | "done";
  children: ReactNode;
}) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

const STATUS: Record<AgentState, string> = {
  running: "Running",
  burned: "Burned",
  finished: "Finished",
};

/** An agent's state beside its name on the night stage. */
export function StatusPill({ state }: { state: AgentState }) {
  return (
    <span className={`status-pill cap status-pill-${state}`}>
      {STATUS[state]}
    </span>
  );
}
