import { useEffect, useId, useState, type FormEvent } from "react";
import toolContract from "../../../../contracts/mcp-tools.json";
import type { BurnApi } from "../../api";
import type { EnrollToken, Instance, Role, RoleSpec } from "../../contracts";
import { CONNECT_GUIDE_URL, joinCommands, type JoinUrls } from "../../join";
import { roleHref } from "../../router";
import { Dialog, Notice } from "../../ui";
import { rolePlural } from "./model";
import {
  Choices,
  Protection,
  TextField,
  parseHosts,
  roleNameConflict,
  rolePattern,
} from "./fields";
import { tokenRecord } from "./tokens";
import "./add-agents.css";

export function AddAgent({
  api,
  roles,
  instances,
  role,
  template,
  suggestedName,
  joinUrls,
  onCreated,
  onToken,
  onClose,
  bare = false,
}: {
  api: BurnApi;
  roles: Role[];
  instances: Instance[];
  /** What the join commands set besides the token, so burn run can connect. */
  joinUrls?: JoinUrls;
  /** Mint another join token for this existing role; capabilities unchanged. */
  role?: Role;
  /**
   * Prefill the form from an existing role's capabilities, usually a burned
   * one being rebuilt. Every field stays editable, so this is a starting
   * point rather than a copy, and it creates a new role.
   */
  template?: Role;
  suggestedName?: string;
  onCreated: (role: Role) => void;
  /** The new token's record: never the token itself. */
  onToken?: (record: EnrollToken) => void;
  onClose: () => void;
  /**
   * Without its own dialog, header and Close, to sit inside another
   * dialog. The flow and its token handling are the same.
   */
  bare?: boolean;
}) {
  const [name, setName] = useState(role?.name ?? suggestedName ?? "");
  const [tools, setTools] = useState<string[]>(template?.tools ?? []);
  const [credentials, setCredentials] = useState<string[]>(
    template?.credentials ?? [],
  );
  const [mayMessage, setMayMessage] = useState<string[]>(
    template?.may_message ?? [],
  );
  const [maySpawn, setMaySpawn] = useState<string[]>(template?.may_spawn ?? []);
  const [egress, setEgress] = useState((template?.egress ?? []).join(", "));
  const [owner, setOwner] = useState(template?.owner ?? "");
  const [canaries, setCanaries] = useState(template?.canaries ?? true);
  const [autoBurn, setAutoBurn] = useState(template?.auto_burn ?? true);
  const [replaceOnBurn, setReplaceOnBurn] = useState(
    template?.replace_on_burn ?? false,
  );
  const [uses, setUses] = useState("1");
  const [ttl, setTtl] = useState("3600");
  const [created, setCreated] = useState(role);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [join, setJoin] = useState<{
    role: string;
    tokenId: string;
    token?: string;
    previousSessions: string[];
  }>();
  const [step, setStep] = useState<
    "form" | "command" | "waiting" | "connected"
  >("form");
  const [copyStatus, setCopyStatus] = useState("");
  const titleId = useId();

  useEffect(() => {
    if (!join || step !== "waiting") return;
    if (
      instances.some(
        (instance) =>
          instance.role === join.role &&
          !join.previousSessions.includes(instance.session_id) &&
          (instance.token_id == null || instance.token_id === join.tokenId),
      )
    )
      setStep("connected");
  }, [instances, join, step]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError("");
    if (!rolePattern.test(name)) {
      setError(
        "Role name must be 1–32 lowercase letters, digits or hyphens, starting with a letter or digit.",
      );
      return;
    }
    if (!created && roles.some((existing) => existing.name === name)) {
      setError(roleNameConflict);
      return;
    }
    const hosts = parseHosts(egress);
    if (!hosts) {
      setError(
        "Enter egress hosts, such as api.example.com, separated by commas or spaces.",
      );
      return;
    }
    const tokenUses = Number(uses),
      tokenTtl = Number(ttl);
    if (
      !Number.isInteger(tokenUses) ||
      tokenUses < 1 ||
      tokenUses > 10000 ||
      !Number.isInteger(tokenTtl) ||
      tokenTtl < 60 ||
      tokenTtl > 2592000
    ) {
      setError(
        "Token uses must be 1–10000 and lifetime must be 60–2592000 whole seconds.",
      );
      return;
    }
    setBusy(true);
    try {
      let target = created;
      if (!target) {
        const spec: RoleSpec = {
          name,
          tools,
          credentials: credentials as RoleSpec["credentials"],
          may_message: mayMessage,
          may_spawn: maySpawn,
          egress: hosts.length ? hosts : null,
          owner: owner.trim() || null,
          canaries,
          auto_burn: autoBurn,
          replace_on_burn: replaceOnBurn,
          // Not on the form, so a copy keeps the original's setting rather
          // than falling back to the API's default.
          ...(template?.references ? { references: template.references } : {}),
          managed_by: "ui",
        };
        target = await api.createRole(spec).catch((error: unknown) => {
          if (
            error instanceof Error &&
            error.message === "API request failed (HTTP 409)."
          ) {
            throw new Error(roleNameConflict);
          }
          throw error;
        });
        setCreated(target);
        onCreated(target);
      }
      const token = await api.createToken(target.name, {
        uses: tokenUses,
        ttl: tokenTtl,
      });
      onToken?.(tokenRecord(token));
      setJoin({
        role: target.name,
        tokenId: token.token_id,
        token: token.token,
        previousSessions: instances.map((instance) => instance.session_id),
      });
      setStep("command");
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not add agent.");
    } finally {
      setBusy(false);
    }
  }

  const commands = join ? joinCommands(join.role, joinUrls) : undefined;
  async function copyToken() {
    if (!join?.token) return;
    try {
      await navigator.clipboard.writeText(join.token);
      setCopyStatus("Token copied.");
    } catch {
      setCopyStatus("Copy unavailable. Select and copy the token above.");
    }
  }
  const forRole = role ? rolePlural(role.name) : "";
  const title = role
    ? `Add ${forRole}`
    : template
      ? `Rebuild from ${template.name}`
      : "Add agents";
  const eyebrow =
    step === "form"
      ? role
        ? `New join token / ${role.name}`
        : "New role and join token"
      : step === "command"
        ? "Join token / shown once"
        : `Joining / ${join?.role ?? ""}`;
  const body =
    step === "form" ? (
      <form className="add-form" onSubmit={(event) => void submit(event)}>
        <p className="dialog-body">
          {role
            ? `Burn mints a join token for ${role.name}. Each agent that joins with it becomes one of the ${forRole}, with its own identity and its own copies of its keys.`
            : "Create a role, then a join token for it. Each agent that joins with the token becomes one running copy of the role, with its own identity and its own copies of its keys."}
        </p>
        {template && !created && (
          <p className="dialog-body">
            Copied from <strong>{template.name}</strong>: the same tools,
            credentials and permissions. Change anything below before creating
            it, or leave it as is to rebuild the agent exactly.
          </p>
        )}
        {!role && (
          <fieldset disabled={busy || Boolean(created)} className="add-fields">
            <div className="add-pair">
              <TextField
                label="Role name"
                className="id"
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
                maxLength={32}
                autoComplete="off"
                spellCheck={false}
                hint="1–32 lowercase letters, digits or hyphens; start with a letter or digit."
              />
              <TextField
                label="Owner"
                value={owner}
                onChange={(event) => setOwner(event.target.value)}
                autoComplete="off"
                hint="The team or person who answers for it."
              />
            </div>
            <Choices
              title="Tools"
              options={toolContract.tools.map((tool) => tool.name)}
              selected={tools}
              onChange={setTools}
            />
            <Choices
              title="Keys"
              hint="Each agent gets its own copy."
              options={["api_key", "aws_key"]}
              selected={credentials}
              onChange={setCredentials}
            />
            <TextField
              label="Can reach (optional)"
              className="id"
              value={egress}
              onChange={(event) => setEgress(event.target.value)}
              placeholder="api.example.com, *.example.org"
              spellCheck={false}
              hint="Hosts, separated by commas or spaces. Leave blank for the default allowlist. Burn's Gateway is always reachable."
            />
            <Choices
              title="Can message"
              options={roles.map((item) => item.name)}
              selected={mayMessage}
              onChange={setMayMessage}
            />
            <Choices
              title="Can start"
              hint="A child never gets more tools than its parent."
              options={roles.map((item) => item.name)}
              selected={maySpawn}
              onChange={setMaySpawn}
            />
            <fieldset className="add-field">
              <legend className="add-label">Protections</legend>
              <Protection
                label="Canary in every session"
                note="A decoy key that proves a leak"
                checked={canaries}
                onChange={setCanaries}
              />
              <Protection
                label="Burn on canary use"
                note="Off means alert and flag only"
                checked={autoBurn}
                disabled={!canaries}
                onChange={setAutoBurn}
              />
              <Protection
                label="Replace a burned agent"
                note="Its finished work stays; the rest is quarantined"
                checked={replaceOnBurn}
                onChange={setReplaceOnBurn}
              />
            </fieldset>
          </fieldset>
        )}
        {created && !role && (
          <p className="dialog-body">
            Role <span className="id">{created.name}</span> exists. Create a new
            join token below.
          </p>
        )}
        <fieldset disabled={busy} className="add-fields">
          <legend className="add-label">Join token</legend>
          <div className="add-pair">
            <TextField
              label="Uses"
              className="num"
              type="number"
              min={1}
              max={10000}
              step={1}
              required
              value={uses}
              onChange={(event) => setUses(event.target.value)}
              hint="How many agents may join with it."
            />
            <TextField
              label="Lifetime (seconds)"
              className="num"
              type="number"
              min={60}
              max={2592000}
              step={1}
              required
              value={ttl}
              onChange={(event) => setTtl(event.target.value)}
              hint="60 to 2,592,000 (30 days)."
            />
          </div>
        </fieldset>
        {error && <Notice tone="error">{error}</Notice>}
        <div className="dialog-actions">
          <button className="btn btn-dark" disabled={busy} type="submit">
            {busy
              ? "Creating…"
              : created
                ? "Create token"
                : "Create role and token"}
          </button>
        </div>
      </form>
    ) : step === "command" ? (
      <div className="add-form">
        <Notice>
          Copy this token now. The token will not be shown again after you
          continue or close this window.
        </Notice>
        <div className="add-token-group">
          <div className="add-token">
            <pre className="add-code add-token-value">
              <code>{join?.token}</code>
            </pre>
            <button
              className="btn btn-sm btn-dark"
              type="button"
              onClick={() => void copyToken()}
            >
              Copy token
            </button>
          </div>
          <p className="add-hint" role="status">
            {copyStatus}
          </p>
        </div>
        <section className="add-commands" aria-label="Join commands">
          <p className="dialog-body">
            Run one of these where your agent runs. Neither holds the token. To
            install the burn CLI there, see{" "}
            <a
              className="text-link"
              href={CONNECT_GUIDE_URL}
              target="_blank"
              rel="noopener noreferrer"
            >
              Connect your agent
            </a>
            .
          </p>
          <h3 className="add-label">Join from a hidden prompt</h3>
          <pre className="add-code">
            <code>{commands?.prompt}</code>
          </pre>
          <h3 className="add-label">Join from the clipboard (macOS)</h3>
          <p className="add-hint">
            Copy this command first, then use Copy token before running it.
          </p>
          <pre className="add-code">
            <code>{commands?.clipboard}</code>
          </pre>
        </section>
        <div className="dialog-actions">
          <button
            className="btn btn-dark"
            type="button"
            onClick={() => {
              setJoin((current) =>
                current ? { ...current, token: undefined } : current,
              );
              setStep("waiting");
            }}
          >
            Continue to waiting
          </button>
        </div>
      </div>
    ) : (
      <div className="add-form add-wait" role="status">
        <p className="add-wait-title">
          {step === "connected"
            ? `Connected · ${join?.role}`
            : `Waiting for ${join?.role} to connect…`}
        </p>
        <p className="add-hint">
          {step === "connected"
            ? `It is listed with the other ${rolePlural(join?.role ?? "")} on the role's page.`
            : "Run the saved command. Burn checks for new agents every five seconds."}
        </p>
        {step === "connected" && join && (
          <div className="dialog-actions">
            <a
              className="btn btn-dark"
              href={roleHref(join.role)}
              onClick={onClose}
            >
              Open {join.role}
            </a>
          </div>
        )}
      </div>
    );
  if (bare)
    return (
      <div className="add-bare">
        <p className="cap add-eyebrow">{eyebrow}</p>
        {body}
      </div>
    );
  return (
    <Dialog
      labelledBy={titleId}
      onClose={onClose}
      busy={busy}
      width={600}
      className="add-agents"
    >
      <div className="add-head">
        <div>
          <p className="cap add-eyebrow">{eyebrow}</p>
          <h2 id={titleId} className="dialog-title">
            {title}
          </h2>
        </div>
        <button
          className="btn btn-sm"
          type="button"
          disabled={busy}
          onClick={onClose}
        >
          Close
        </button>
      </div>
      {body}
    </Dialog>
  );
}
