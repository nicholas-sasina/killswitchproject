import { useCallback, type ReactNode } from "react";
import type { Role } from "../../contracts";
import { useConsole } from "../../data/ConsoleData";
import type { ConsoleAgent, ConsoleModel } from "../../model/console";
import { personLabel } from "../../model/people";
import { ago, clockTime, dayTime, isSameDay } from "../../model/time";
import { usePolling } from "../../polling";
import { agentHref, roleHref } from "../../router";
import { Card, Chip, Facts, Notice } from "../../ui";
import {
  blastSentence,
  replacementOf,
  takeoverOf,
  type Handover,
} from "./story";

function AgentLinks({ agents }: { agents: ConsoleAgent[] }): ReactNode {
  if (!agents.length) return null;
  return agents.map((other, index) => (
    <span key={other.key}>
      {index > 0 && ", "}
      <a
        className="text-link identity-agent"
        href={agentHref(other.sessionId ?? other.key)}
      >
        {other.name}
      </a>
      {other.state !== "running" && (
        <span className="identity-state">
          {" "}
          ({other.state === "burned" ? "burned" : "finished"})
        </span>
      )}
    </span>
  ));
}

/** Role, who started it, its token, parent and children, ids, last call. */
export function IdentityCard({
  agent,
  model,
}: {
  agent: ConsoleAgent;
  model: ConsoleModel;
}) {
  const parent = agent.parent ? model.byKey.get(agent.parent) : undefined;
  const children = agent.children
    .map((key) => model.byKey.get(key))
    .filter((child): child is ConsoleAgent => Boolean(child));
  const replaces = agent.instance?.replaces
    ? model.byKey.get(agent.instance.replaces)
    : undefined;
  const replacedBy = agent.instance?.replaced_by
    ? model.byKey.get(agent.instance.replaced_by)
    : undefined;
  const by = personLabel(agent.createdBy);
  const started = dayTime(agent.createdAt, model.now);
  const when = (value?: string | null) =>
    isSameDay(value, model.now) ? clockTime(value) : dayTime(value, model.now);
  return (
    <Card title="Identity" pad="narrow" className="agent-identity">
      <Facts
        rows={[
          {
            label: "Role",
            value: (
              <a className="text-link" href={roleHref(agent.role)}>
                {agent.role}
              </a>
            ),
          },
          {
            label: by ? "Started by" : "Started",
            value: by ? [by, started].filter(Boolean).join(", ") : started,
          },
          { label: "Join token", value: agent.tokenId, mono: true },
          {
            label: "Spawned by",
            value: parent ? (
              <AgentLinks agents={[parent]} />
            ) : (
              agent.parentSession
            ),
            mono: !parent,
          },
          {
            label: "Spawned",
            value: children.length ? <AgentLinks agents={children} /> : null,
          },
          {
            label: "Took over from",
            value: replaces ? (
              <AgentLinks agents={[replaces]} />
            ) : (
              agent.instance?.replaces
            ),
          },
          {
            label: "Replaced by",
            value: replacedBy ? (
              <AgentLinks agents={[replacedBy]} />
            ) : (
              agent.instance?.replaced_by
            ),
          },
          { label: "Agent id", value: agent.agentId, mono: true },
          { label: "Session", value: agent.sessionId, mono: true },
          {
            label: "Last call",
            value:
              agent.state === "running"
                ? ago(agent.lastSeen, model.now)
                : when(agent.lastSeen),
          },
          {
            label: "Finished",
            value:
              agent.state === "finished"
                ? when(agent.instance?.exited_at)
                : null,
          },
        ]}
      />
    </Card>
  );
}

function HandoverPart({ handover }: { handover: Handover }) {
  const headline =
    handover.other && handover.headline.startsWith(handover.other.name) ? (
      <>
        <a className="text-link" href={agentHref(handover.other.key)}>
          {handover.other.name}
        </a>
        {handover.headline.slice(handover.other.name.length)}
      </>
    ) : handover.other && handover.headline.endsWith(handover.other.name) ? (
      <>
        {handover.headline.slice(
          0,
          handover.headline.length - handover.other.name.length,
        )}
        <a className="text-link" href={agentHref(handover.other.key)}>
          {handover.other.name}
        </a>
      </>
    ) : (
      handover.headline
    );
  return (
    <div className="after-part">
      <h3 className="after-headline">{headline}</h3>
      {handover.detail && <p className="after-detail">{handover.detail}</p>}
      {handover.quarantined.length > 0 && (
        <>
          <p className="after-label">{handover.quarantineLabel}</p>
          <ul className="after-chips" aria-label={handover.quarantineLabel}>
            {handover.quarantined.map((id) => (
              <li key={id}>
                <Chip>{id}</Chip>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

/** The CloudTrail calls made with its AWS keys, every ten seconds. */
function BlastPart({ agentId }: { agentId: string }) {
  const { api } = useConsole();
  const load = useCallback(
    (signal: AbortSignal) => api.blastRadius(agentId, signal),
    [api, agentId],
  );
  const { data, error } = usePolling(load, 10_000);
  const blast = data && data.agent_id === agentId ? data : undefined;
  return (
    <div className="after-part">
      <h3 className="after-headline">Blast radius</h3>
      {blast ? (
        <p className="after-detail">{blastSentence(blast)}</p>
      ) : error ? null : (
        <p className="after-detail">
          Checking CloudTrail for calls with its AWS keys. CloudTrail runs a few
          minutes behind.
        </p>
      )}
      {error && (
        <Notice tone="error">
          CloudTrail could not be checked: {error}
          {blast ? " Showing the last answer." : ""}
        </Notice>
      )}
    </div>
  );
}

/** "AFTER THE BURN": its replacement (#105) and its blast radius. */
export function AfterBurnCard({
  agent,
  model,
  role,
}: {
  agent: ConsoleAgent;
  model: ConsoleModel;
  role?: Role;
}) {
  const handover = replacementOf(agent, model, role);
  return (
    <Card title="After the burn" pad="narrow" className="agent-after">
      {handover && <HandoverPart handover={handover} />}
      <BlastPart agentId={agent.agentId} />
    </Card>
  );
}

/** For a replacement: what it took over from the agent it replaced. */
export function TakeoverCard({
  agent,
  model,
}: {
  agent: ConsoleAgent;
  model: ConsoleModel;
}) {
  const handover = takeoverOf(agent, model);
  if (!handover) return null;
  return (
    <Card title="Replacement" pad="narrow" className="agent-after">
      <HandoverPart handover={handover} />
    </Card>
  );
}
