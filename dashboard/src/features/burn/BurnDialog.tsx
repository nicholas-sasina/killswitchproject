/**
 * Burn one agent, or every running agent of a role, after saying exactly
 * what will happen. The dialog makes the request itself:
 *
 *   an agent with a session   POST /burns {scope: "instance"}  (this is the
 *                             burn that can start a replacement)
 *   a scenario agent with none POST /burn {agent_id}
 *   a role                     POST /burns {scope: "role"}
 *
 * Then it reports the result with the console's `recordBurn` (so every page
 * shows the agents burned at once) and calls `onBurned`. A page that keeps
 * the dialog open sees the containment checklist tick inside it.
 *
 * A failed request leaves the dialog open with a plain sentence: what went
 * wrong, that the outcome is not confirmed, and what to check first.
 */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ApiError } from "../../api";
import type { BurnResult, LedgerEntry } from "../../contracts";
import { useConsole } from "../../data/ConsoleData";
import { findAgent } from "../../model/console";
import { clockTime } from "../../model/time";
import { Dialog, Notice } from "../../ui";
import { ContainmentChecklist } from "./Checklist";
import {
  agentPlan,
  containmentSteps,
  credentialsFor,
  rolePlan,
  type PlanLine,
} from "./steps";
import type { BurnDialogProps, BurnOutcome } from "./types";
import "./burn.css";

export type { BurnDialogProps, BurnOutcome, BurnTarget } from "./types";

/** Wide enough that the plan's lines rarely wrap. */
const DIALOG_WIDTH = 576;

/** The most the API takes for a burn's reason. */
export const REASON_MAX = 200;

function PlanList({ id, lines }: { id: string; lines: PlanLine[] }) {
  return (
    <ul id={id} className="burn-plan">
      {lines.map((line) => (
        <li key={line.id}>
          {line.parts.map((part, index) =>
            typeof part === "string" ? (
              part
            ) : (
              <span key={index} className="id">
                {part.key}
              </span>
            ),
          )}
        </li>
      ))}
    </ul>
  );
}

/** What to say when the request fails: plain, with what to do next. */
export function burnErrorText(error: unknown, who: string): string {
  if (error instanceof ApiError && error.status === 404)
    return `Burn couldn't find ${who} (HTTP 404). It may have finished or been burned already; check its status before retrying.`;
  const message =
    error instanceof Error && error.message
      ? error.message
      : "The burn request failed.";
  return `${message} Outcome not confirmed. Check ${who}'s status before retrying.`;
}

export function BurnDialog({ target, onClose, onBurned }: BurnDialogProps) {
  const { api, model, recordBurn } = useConsole();
  const titleId = useId();
  const planId = useId();
  const reasonId = useId();
  const cancel = useRef<HTMLButtonElement>(null);
  const finish = useRef<HTMLButtonElement>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [answer, setAnswer] = useState<{
    outcome: BurnOutcome;
    untouched: number;
  }>();

  const agent = target.scope === "agent" ? target.agent : undefined;
  const roleName = target.scope === "role" ? target.role : agent!.role;
  const group = model.roleByName.get(roleName);
  const name = agent ? agent.name : `every ${roleName}`;

  // The agent's ledger, read once: its owned and shared keys by name.
  const [entries, setEntries] = useState<LedgerEntry[]>();
  const agentId = agent?.agentId;
  useEffect(() => {
    if (!agentId) return;
    const controller = new AbortController();
    api
      .ledger(agentId, controller.signal)
      .then((ledger) => {
        if (!controller.signal.aborted && ledger.agent_id === agentId)
          setEntries(ledger.entries);
      })
      // Without it the plan speaks generally; the burn itself is unaffected.
      .catch(() => {});
    return () => controller.abort();
  }, [api, agentId]);

  const lines = useMemo(() => {
    if (agent) {
      const children = agent.children
        .map((key) => model.byKey.get(key))
        .filter((child) => child?.state === "running")
        .map((child) => child!.name);
      return agentPlan({
        agent,
        entries,
        credentials: credentialsFor(agent, model),
        childrenRunning: children,
        replaceOnBurn: Boolean(group?.role?.replace_on_burn),
      });
    }
    const running = group?.running ?? [];
    const keys = new Set(running.map((item) => item.key));
    const elsewhere = model.agents
      .filter(
        (item) =>
          item.state === "running" &&
          item.role !== roleName &&
          item.parent !== undefined &&
          keys.has(item.parent),
      )
      .map((item) => item.name);
    return rolePlan({
      role: roleName,
      running: running.map((item) => item.name),
      credentials: group?.role?.credentials ?? [],
      childrenElsewhere: elsewhere,
      replaceOnBurn: Boolean(group?.role?.replace_on_burn),
    });
  }, [agent, entries, group, model, roleName]);

  const nothingToBurn =
    target.scope === "role" ? !group?.running.length : false;
  const who = agent ? agent.name : `each ${roleName}`;

  async function confirm() {
    if (busy || nothingToBurn) return;
    setBusy(true);
    setError("");
    const why = reason.trim();
    const withReason = why ? { reason: why } : {};
    try {
      let burned: BurnResult[];
      let failed: BurnOutcome["failed"] = [];
      if (target.scope === "agent") {
        if (target.agent.sessionId) {
          const answer = await api.burnScope({
            scope: "instance",
            id: target.agent.sessionId,
            ...withReason,
          });
          burned = answer.burned;
          failed = answer.failed ?? [];
        } else {
          burned = [
            await api.burn({ agent_id: target.agent.agentId, ...withReason }),
          ];
        }
      } else {
        const answer = await api.burnScope({
          scope: "role",
          id: target.role,
          ...withReason,
        });
        burned = answer.burned;
        failed = answer.failed ?? [];
      }
      // Counted before recordBurn rebuilds the model without them.
      const stopping = agent
        ? agent.state === "running"
          ? 1
          : 0
        : (group?.running.length ?? 0);
      const untouched = Math.max(0, model.counts.running - stopping);
      recordBurn(burned);
      const outcome: BurnOutcome = {
        target,
        burned,
        failed,
        at: new Date().toISOString(),
        ...withReason,
      };
      setAnswer({ outcome, untouched });
      setBusy(false);
      onBurned?.(outcome);
    } catch (caught) {
      setError(burnErrorText(caught, who));
      setBusy(false);
    }
  }

  // The result replaces the buttons; keep focus inside the dialog.
  useEffect(() => {
    if (answer) finish.current?.focus();
  }, [answer]);

  if (answer) {
    const { outcome, untouched } = answer;
    const children = agent
      ? agent.children
          .map((key) => model.byKey.get(key))
          .filter((child) => child?.state === "running")
          .map((child) => child!.name)
      : [];
    const steps = containmentSteps(outcome, {
      untouched,
      childrenLeft: children,
      nameOf: (id) => findAgent(model, id)?.name,
    });
    return (
      <Dialog
        labelledBy={titleId}
        onClose={onClose}
        className="burn-dialog"
        width={DIALOG_WIDTH}
        initialFocus={finish}
      >
        <p className="cap dialog-eyebrow">
          Contained at {clockTime(outcome.at)}
        </p>
        <h2 id={titleId} className="dialog-title">
          Burned {name}
        </h2>
        <ContainmentChecklist steps={steps} done={0} animate />
        <div className="dialog-actions">
          <button
            ref={finish}
            className="btn btn-dark"
            type="button"
            onClick={onClose}
          >
            Done
          </button>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog
      labelledBy={titleId}
      describedBy={planId}
      onClose={onClose}
      busy={busy}
      initialFocus={cancel}
      className="burn-dialog"
      width={DIALOG_WIDTH}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void confirm();
        }}
      >
        <p className="cap dialog-eyebrow">
          {agent ? "Burn one agent" : "Burn a role"}
        </p>
        <h2 id={titleId} className="dialog-title">
          Burn {name}?
        </h2>
        <PlanList id={planId} lines={lines} />
        {!nothingToBurn && (
          <div className="burn-reason">
            <label htmlFor={reasonId}>
              Reason <span className="burn-reason-hint">(optional)</span>
            </label>
            <input
              id={reasonId}
              value={reason}
              maxLength={REASON_MAX}
              disabled={busy}
              autoComplete="off"
              placeholder="What did you see? Shown on Incidents."
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
        )}
        {error && <Notice tone="error">{error}</Notice>}
        {busy && (
          <p className="visually-hidden" role="status">
            Burning {name}. Keep this window open for the result.
          </p>
        )}
        <div className="dialog-actions">
          <button
            className="btn btn-ember"
            type="submit"
            disabled={busy || nothingToBurn}
          >
            {busy ? "Burning…" : `Burn ${name}`}
          </button>
          <button
            ref={cancel}
            className="btn"
            type="button"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}
