import { useEffect, useId, useMemo, useState } from "react";
import { useConsole } from "../../data/ConsoleData";
import { joinable } from "../../fleet";
import { CONNECT_GUIDE_URL } from "../../join";
import { agentHref } from "../../router";
import { Dialog, Segmented } from "../../ui";
import {
  HARNESSES,
  agentGets,
  firstNewAgent,
  harnessOf,
  nextAgentName,
  runCommand,
  setupCommands,
  targetRole,
  type Harness,
} from "./connect";
import { accessLevel } from "./model";
import { TokenDialog } from "./TokenDialog";
import "./add-agents.css";

export interface AddAgentsDialogProps {
  /** Preselect this role; otherwise the agent is grouped by its harness. */
  role?: string;
  onClose: () => void;
}

/** A command in a block, with a Copy button that copies only that text. */
function Command({ label, text }: { label: string; text: string }) {
  const [status, setStatus] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setStatus("Copied.");
    } catch {
      setStatus("Copy unavailable. Select and copy the command.");
    }
  }
  return (
    <div className="connect-command">
      <pre className="add-code" aria-label={label}>
        <code>{text}</code>
      </pre>
      <button
        className="btn btn-sm"
        type="button"
        aria-label={`Copy ${label.toLowerCase()}`}
        onClick={() => void copy()}
      >
        Copy
      </button>
      <span className="visually-hidden" role="status">
        {status}
      </span>
    </div>
  );
}

/**
 * Connect an agent, as the navigation and a role page open it. The default
 * path has no form: install burn and sign in once, then `burn run` the
 * harness, with a role only if one is chosen. It waits on the console's
 * shared /instances poll for the first new agent. For CI, containers and
 * swarms, a disclosure holds the join token flow.
 */
export function AddAgentsDialog({ role, onClose }: AddAgentsDialogProps) {
  const { roles, model, instancesLoaded, joinUrls } = useConsole();
  const titleId = useId();
  const tokenSelect = useId();
  const roleSelect = useId();
  const [harness, setHarness] = useState<Harness>("claude");
  const [chosen, setChosen] = useState(role ?? "");
  const [tokenRole, setTokenRole] = useState(role ?? "");
  const [tokens, setTokens] = useState(false);
  const [openedAt] = useState(() => Date.now());
  const [known, setKnown] = useState<ReadonlySet<string> | undefined>(() =>
    instancesLoaded
      ? new Set(model.agents.map((agent) => agent.key))
      : undefined,
  );
  useEffect(() => {
    if (!known && instancesLoaded)
      setKnown(new Set(model.agents.map((agent) => agent.key)));
  }, [known, instancesLoaded, model.agents]);

  const choices = useMemo(() => roles.filter(joinable), [roles]);
  const record = choices.find((item) => item.name === chosen);
  const group = harnessOf(harness).group;
  const target = targetRole(harness, chosen);
  const name = nextAgentName(model, target);
  const connected = firstNewAgent(
    model.agents,
    target,
    openedAt,
    known ?? new Set(),
  );

  return (
    <Dialog
      labelledBy={titleId}
      onClose={onClose}
      width={640}
      className="add-agents connect"
    >
      <div className="add-head">
        <div>
          <p className="cap add-eyebrow">From your own harness</p>
          <h2 id={titleId} className="dialog-title">
            Connect an agent
          </h2>
        </div>
        <button className="btn btn-sm" type="button" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="add-form">
        <p className="dialog-body">
          Run your agent under <span className="id">burn run</span>, from Claude
          Code, OpenCode, Codex or a script. No role is needed; choose one only
          to narrow what it may do. The CLI&apos;s guide has{" "}
          <a
            className="text-link"
            href={CONNECT_GUIDE_URL}
            target="_blank"
            rel="noopener noreferrer"
          >
            the full steps
          </a>
          .
        </p>
        <section className="connect-step" aria-labelledby={`${titleId}-once`}>
          <h3 id={`${titleId}-once`} className="add-label">
            Once per machine
          </h3>
          <Command label="Setup commands" text={setupCommands(joinUrls)} />
        </section>
        <section className="connect-step" aria-labelledby={`${titleId}-run`}>
          <h3 id={`${titleId}-run`} className="add-label">
            Run your agent
          </h3>
          <Segmented
            label="Harness"
            options={HARNESSES.map(({ value, label }) => ({ value, label }))}
            value={harness}
            onChange={setHarness}
          />
          <div className="add-field">
            <label className="add-label" htmlFor={roleSelect}>
              Role (optional)
            </label>
            <select
              id={roleSelect}
              className="add-input"
              value={chosen}
              onChange={(event) => setChosen(event.target.value)}
            >
              <option value="">None (Full access, grouped as {group})</option>
              {choices.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name} · {accessLevel(item)}
                </option>
              ))}
            </select>
          </div>
          <Command label="Run command" text={runCommand(harness, chosen)} />
          <p className="add-hint connect-gets">
            {agentGets(harness, name, record)}
          </p>
          <p className="connect-wait" role="status">
            {connected ? (
              <>
                Connected ·{" "}
                <a className="text-link" href={agentHref(connected.key)}>
                  {connected.name}
                </a>
              </>
            ) : (
              `Waiting for the first new ${target} agent…`
            )}
          </p>
        </section>
        <section className="connect-tokens">
          <button
            className="add-disclosure"
            type="button"
            aria-expanded={tokens}
            onClick={() => setTokens((open) => !open)}
          >
            For CI, containers or swarms: use a join token
          </button>
          {tokens && (
            <div className="connect-token-flow">
              <div className="add-field">
                <label className="add-label" htmlFor={tokenSelect}>
                  Role for the token
                </label>
                <select
                  id={tokenSelect}
                  className="add-input"
                  value={tokenRole}
                  onChange={(event) => setTokenRole(event.target.value)}
                >
                  <option value="">A new role</option>
                  {choices.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.name} · {accessLevel(item)}
                    </option>
                  ))}
                </select>
              </div>
              <TokenDialog
                key={tokenRole}
                role={tokenRole || undefined}
                onClose={onClose}
                bare
              />
            </div>
          )}
        </section>
      </div>
    </Dialog>
  );
}
