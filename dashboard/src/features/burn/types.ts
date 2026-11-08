/**
 * The contract between the pages that burn and the dialog that does it.
 * The Agent page and the Role page both open BurnDialog; the dialog makes
 * the request itself and reports what happened.
 */
import type { BurnResult } from "../../contracts";
import type { ConsoleAgent } from "../../model/console";

/** What to burn: one agent, or every running agent of a role. */
export type BurnTarget =
  { scope: "agent"; agent: ConsoleAgent } | { scope: "role"; role: string };

/** What the API answered, as the dialog hands it back. */
export interface BurnOutcome {
  target: BurnTarget;
  /** One entry per agent the API burned (POST /burn gives one). */
  burned: BurnResult[];
  /** Agents the API revoked but could not finish burning (POST /burns). */
  failed: { agent_id?: string; error?: string }[];
  /** When the answer arrived. */
  at: string;
  /** The reason the person gave, if any. */
  reason?: string;
}

export interface BurnDialogProps {
  target: BurnTarget;
  onClose: () => void;
  /**
   * Called once the API has answered. The dialog has already reported the
   * results with the console's recordBurn, so every page shows the agents
   * burned at once.
   */
  onBurned?: (outcome: BurnOutcome) => void;
}

/** One line of the containment checklist. */
export interface ContainmentStep {
  id: string;
  title: string;
  sub?: string;
  /** The first, ember-tinted step: the badge revoked. */
  hot?: boolean;
  /** Set when this step did not finish: what to do about it. */
  error?: string;
}
