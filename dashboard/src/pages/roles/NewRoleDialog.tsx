import { useId, useState, type FormEvent } from "react";
import { useConsole } from "../../data/ConsoleData";
import type { RoleSpec } from "../../contracts";
import { roleHref } from "../../router";
import { Dialog, Notice } from "../../ui";
import {
  Choices,
  Protection,
  TextField,
  parseHosts,
  roleNameConflict,
  rolePattern,
} from "./fields";
import { FULL_ACCESS_TOOLS, fullAccessRole, type Access } from "./model";
import "./add-agents.css";

/**
 * New role: a name, and Full access unless it is narrowed. A role is
 * optional and reusable; agents join it with Connect an agent. Full access
 * sends the same body burn run sends when it names a role itself.
 */
export function NewRoleDialog({ onClose }: { onClose: () => void }) {
  const { api, roles, refreshRoles } = useConsole();
  const titleId = useId();
  const [name, setName] = useState("");
  const [access, setAccess] = useState<Access>("Full access");
  const [tools, setTools] = useState<string[]>([...FULL_ACCESS_TOOLS]);
  const [credentials, setCredentials] = useState<string[]>(["aws_key"]);
  const [egress, setEgress] = useState("");
  const [more, setMore] = useState(false);
  const [canaries, setCanaries] = useState(true);
  const [autoBurn, setAutoBurn] = useState(true);
  const [replaceOnBurn, setReplaceOnBurn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

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
    if (roles.some((existing) => existing.name === name)) {
      setError(roleNameConflict);
      return;
    }
    const hosts = parseHosts(egress);
    if (!hosts) {
      setError(
        "Enter websites as hosts, such as api.example.com, separated by commas or spaces.",
      );
      return;
    }
    const protections = {
      canaries,
      auto_burn: autoBurn,
      replace_on_burn: replaceOnBurn,
    };
    const spec: RoleSpec =
      access === "Full access"
        ? { ...fullAccessRole(name), ...protections }
        : {
            ...fullAccessRole(name),
            tools,
            credentials: credentials as RoleSpec["credentials"],
            egress: hosts.length ? hosts : null,
            ...protections,
          };
    setBusy(true);
    try {
      await api.createRole(spec);
      refreshRoles();
      onClose();
      window.location.hash = roleHref(name);
    } catch (caught) {
      setError(
        caught instanceof Error &&
          caught.message === "API request failed (HTTP 409)."
          ? roleNameConflict
          : caught instanceof Error
            ? caught.message
            : "Could not create the role.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      labelledBy={titleId}
      onClose={onClose}
      busy={busy}
      width={560}
      className="add-agents"
    >
      <div className="add-head">
        <div>
          <p className="cap add-eyebrow">Optional, and reusable</p>
          <h2 id={titleId} className="dialog-title">
            New role
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
      <form className="add-form" onSubmit={(event) => void submit(event)}>
        <p className="dialog-body">
          A role groups agents and can narrow what they may do. Connect as many
          agents to it as you like.
        </p>
        <fieldset disabled={busy} className="add-fields">
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
          <fieldset className="add-field">
            <legend className="add-label">Access</legend>
            <div className="add-chips">
              {(["Full access", "Limited"] as const).map((option) => (
                <label key={option} className="add-chip">
                  <input
                    type="radio"
                    name={`${titleId}-access`}
                    checked={access === option}
                    onChange={() => setAccess(option)}
                  />
                  <span>{option}</span>
                </label>
              ))}
            </div>
            <p className="add-hint">
              {access === "Full access"
                ? "Every tool and its own AWS key, which also reaches its model. Nothing is narrowed."
                : "Choose what its agents may use."}
            </p>
          </fieldset>
          {access === "Limited" && (
            <>
              <Choices
                title="Tools"
                options={[...FULL_ACCESS_TOOLS]}
                selected={tools}
                onChange={setTools}
              />
              <Choices
                title="Keys"
                hint="Each agent gets its own copy. Without aws_key it has no model through Burn."
                options={["api_key", "aws_key"]}
                selected={credentials}
                onChange={setCredentials}
              />
              <TextField
                label="Websites it can reach (optional)"
                className="id"
                value={egress}
                onChange={(event) => setEgress(event.target.value)}
                placeholder="api.example.com, *.example.org"
                spellCheck={false}
                hint="Leave blank for the default allowlist. Burn's Gateway is always reachable."
              />
            </>
          )}
          <button
            className="add-disclosure"
            type="button"
            aria-expanded={more}
            onClick={() => setMore((open) => !open)}
          >
            More
            <span className="add-disclosure-mark" aria-hidden="true">
              {more ? "\u2212" : "+"}
            </span>
          </button>
          {more && (
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
          )}
        </fieldset>
        {error && <Notice tone="error">{error}</Notice>}
        <div className="dialog-actions">
          <button className="btn btn-dark" disabled={busy} type="submit">
            {busy ? "Creating…" : "Create role"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
