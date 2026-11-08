/**
 * One agent: what it is and its state, every key it saw, everything it did,
 * and what a burn would do or did. Reached from a square, a role row or an
 * incident at #/agents/<session_id> (an agent id also resolves).
 *
 * Its own requests run only while it is open: the ledger every five
 * seconds, and once it is burned, CloudTrail every ten. Everything else
 * comes from the console's shared data.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { BurnResult } from "../../contracts";
import { useConsole } from "../../data/ConsoleData";
import { BurnDialog } from "../../features/burn/BurnDialog";
import { ContainmentChecklist } from "../../features/burn/Checklist";
import { containmentSteps, credentialsFor } from "../../features/burn/steps";
import type { BurnOutcome, ContainmentStep } from "../../features/burn/types";
import { burnTrigger, type BurnTrigger } from "../../model/burns";
import { findAgent, type ConsoleAgent } from "../../model/console";
import { clockTime } from "../../model/time";
import { usePolling } from "../../polling";
import { roleHref } from "../../router";
import {
  Card,
  Columns,
  EmptyState,
  Hero,
  Notice,
  StatusPill,
  useReducedMotion,
} from "../../ui";
import { AfterBurnCard, IdentityCard, TakeoverCard } from "./AsideCards";
import { evidenceRefs } from "./ledger";
import { LedgerCard } from "./LedgerCard";
import {
  agentEvents,
  beforeBurnSteps,
  finishedNote,
  heroLede,
  timelineItems,
} from "./story";
import { TimelineCard } from "./TimelineCard";
import "./agent.css";

export function AgentPage({ sessionId }: { sessionId: string }) {
  const { model, instancesLoaded, updatedAt } = useConsole();
  const agent = findAgent(model, sessionId);
  return (
    <div className="agent-page">
      {agent ? (
        <AgentView agent={agent} />
      ) : (
        <MissingAgent loading={!instancesLoaded || !updatedAt} />
      )}
    </div>
  );
}

function MissingAgent({ loading }: { loading: boolean }) {
  return (
    <>
      <Hero
        crumbs={[{ label: "Roles", href: "#/roles" }, { label: "Agent" }]}
        title="Agent"
      />
      <Columns>
        <Card title="Agent">
          {loading ? (
            <Notice>Loading this agent…</Notice>
          ) : (
            <EmptyState
              action={
                <a className="btn btn-sm" href="#/roles">
                  See every role
                </a>
              }
            >
              No agent has this id. The link may be wrong, or the agent may be
              older than anything Burn still lists.
            </EmptyState>
          )}
        </Card>
      </Columns>
    </>
  );
}

/** A burn made from this page: what the API answered, and who it left alone. */
interface FreshBurn {
  outcome: BurnOutcome;
  untouched: number;
}

function AgentView({ agent }: { agent: ConsoleAgent }) {
  const data = useConsole();
  const { api, model, events, sentenceContext, history } = data;
  const reduced = useReducedMotion();
  const [confirming, setConfirming] = useState(false);
  const [fresh, setFresh] = useState<FreshBurn>();
  const [replays, setReplays] = useState(0);
  const containment = useRef<HTMLDivElement>(null);

  const loadLedger = useCallback(
    (signal: AbortSignal) => api.ledger(agent.agentId, signal),
    [api, agent.agentId],
  );
  const ledger = usePolling(loadLedger, 5_000);
  const entries =
    ledger.data && ledger.data.agent_id === agent.agentId
      ? ledger.data.entries
      : undefined;

  const mine = useMemo(
    () => agentEvents(events, agent, model),
    [events, agent, model],
  );
  const nameOf = useCallback(
    (agentId: string) => findAgent(model, agentId)?.name,
    [model],
  );
  const group = model.roleByName.get(agent.role);
  const childrenRunning = agent.children
    .map((key) => model.byKey.get(key))
    .filter((child) => child?.state === "running")
    .map((child) => child!.name);

  // Until its revocation's event arrives, a burn from here was by "you".
  const trigger = burnTrigger(agent, mine);
  const cause: BurnTrigger =
    fresh && (trigger.kind === "unknown" || trigger.kind === "reason")
      ? {
          kind: "person",
          who: "you",
          reason:
            fresh.outcome.reason ??
            (trigger.kind === "reason" ? trigger.reason : ""),
        }
      : trigger;
  const lede = heroLede({
    agent,
    model,
    entries,
    events: mine,
    cause,
    burnedAt: fresh?.outcome.at,
  });
  const evidence = useMemo(
    () => evidenceRefs(entries, agent.agentId),
    [entries, agent.agentId],
  );
  const items = useMemo(
    () =>
      timelineItems(
        mine,
        sentenceContext,
        agent.state === "burned" ? agent.burnedAt : undefined,
        evidence,
      ),
    [mine, sentenceContext, agent.state, agent.burnedAt, evidence],
  );
  const joined = mine.some(
    (event) => event.action === "started" || event.action === "issued",
  );
  const before = history.from
    ? Boolean(agent.createdAt && agent.createdAt < history.from)
    : !history.complete;
  const timelineNote =
    !joined && before
      ? `${agent.name} started before the history the console loads, so its earliest events aren't shown.`
      : undefined;

  // The containment card: what a burn would do, or what this one did.
  let containTitle = "";
  let steps: ContainmentStep[] = [];
  let ticking = false;
  if (agent.state === "running") {
    containTitle = "If you burn it";
    steps = beforeBurnSteps({
      agentId: agent.agentId,
      entries,
      credentials: credentialsFor(agent, model),
      othersRunning: Math.max(0, model.counts.running - 1),
      childrenRunning,
    });
  } else if (agent.state === "burned") {
    const target = { scope: "agent" as const, agent };
    const record =
      data.burns[agent.key] ??
      (agent.sessionId ? data.burns[agent.sessionId] : undefined) ??
      data.burns[agent.agentId];
    const incident = data.incidents.find(
      (item) => item.agent.key === agent.key,
    );
    const known = agent.burnedAtFrom === "event";
    const fromHistory: BurnResult = {
      agent_id: agent.agentId,
      revoked: true,
      sessions_revoked: agent.sessionId ? [agent.sessionId] : [],
      rotated: incident?.rotated ?? [],
      flagged: incident?.flagged ?? [],
    };
    const outcome: Pick<BurnOutcome, "target" | "burned" | "failed"> =
      fresh?.outcome ??
      (record
        ? { target, burned: [record.result], failed: [] }
        : {
            target,
            burned: [fromHistory],
            failed:
              incident?.status === "rotation-failed"
                ? [
                    {
                      agent_id: agent.agentId,
                      error: "Burn couldn't rotate its keys",
                    },
                  ]
                : [],
          });
    const burnedAt = agent.burnedAt;
    steps = containmentSteps(outcome, {
      untouched: fresh?.untouched,
      childrenLeft: childrenRunning,
      nameOf,
      refused: Boolean(
        burnedAt &&
        mine.some(
          (event) => event.action === "refused" && event.ts >= burnedAt,
        ),
      ),
    });
    // Without its burn's events, say nothing about keys rather than "none".
    if (!fresh && !record && !known)
      steps = steps.filter((step) => step.id !== "keys");
    const at =
      fresh?.outcome.at ??
      record?.at ??
      (agent.burnedAtFrom !== "last-seen" ? agent.burnedAt : undefined);
    containTitle = at ? `Contained at ${clockTime(at)}` : "Contained";
    ticking = Boolean(fresh) || replays > 0;
  }

  // After a burn from here, the checklist is where the news is.
  useEffect(() => {
    if (fresh) containment.current?.focus();
  }, [fresh]);

  const actions =
    agent.state === "running" ? (
      <button
        type="button"
        className="btn btn-ember"
        onClick={() => setConfirming(true)}
      >
        Burn {agent.name}
      </button>
    ) : agent.state === "burned" && !reduced && steps.length ? (
      <button
        type="button"
        className="btn btn-ghost"
        onClick={() => {
          setReplays((count) => count + 1);
          containment.current?.focus();
        }}
      >
        Replay the burn
      </button>
    ) : undefined;

  return (
    <>
      <Hero
        className="agent-hero"
        crumbs={[
          { label: "Roles", href: "#/roles" },
          { label: agent.role, href: roleHref(agent.role) },
          { label: agent.name },
        ]}
        title={agent.name}
        status={<StatusPill state={agent.state} />}
        lede={lede}
        actions={actions}
      />
      <Columns
        aside={
          <>
            {agent.state === "finished" ? (
              <Card title="Finished" pad="narrow" className="agent-finished">
                <p>{finishedNote(agent, group?.role?.managed_by)}</p>
              </Card>
            ) : (
              <div
                ref={containment}
                tabIndex={-1}
                className="agent-containment"
                aria-label={containTitle}
              >
                <Card title={containTitle} pad="narrow">
                  <ContainmentChecklist
                    key={`${fresh?.outcome.at ?? ""}:${replays}`}
                    steps={steps}
                    done={
                      agent.state === "burned" && !ticking ? steps.length : 0
                    }
                    animate={agent.state === "burned" && ticking}
                  />
                </Card>
              </div>
            )}
            <IdentityCard agent={agent} model={model} />
            <TakeoverCard agent={agent} model={model} />
            {agent.state === "burned" && (
              <AfterBurnCard agent={agent} model={model} role={group?.role} />
            )}
          </>
        }
      >
        <LedgerCard
          agent={agent}
          entries={entries}
          error={ledger.error}
          events={mine}
          nameOf={nameOf}
        />
        <TimelineCard
          agent={agent}
          items={items}
          now={model.now}
          note={timelineNote}
        />
      </Columns>
      {confirming && agent.state === "running" && (
        <BurnDialog
          target={{ scope: "agent", agent }}
          onClose={() => setConfirming(false)}
          onBurned={(outcome) => {
            const stopping = 1;
            setFresh({
              outcome,
              untouched: Math.max(0, model.counts.running - stopping),
            });
            setConfirming(false);
          }}
        />
      )}
    </>
  );
}
