import { useEffect, useState, type FormEvent } from "react";
import toolContract from "../../../contracts/mcp-tools.json";
import type { BurnApi } from "../api";
import type { Instance, Role, RoleSpec } from "../contracts";
import { ErrorMessage } from "./common";
import { SwarmDialog } from "./SwarmDialog";

const rolePattern = /^[a-z0-9][a-z0-9-]{0,31}$/;

function Choices({
  title,
  options,
  selected,
  onChange,
}: {
  title: string;
  options: string[];
  selected: string[];
  onChange: (selected: string[]) => void;
}) {
  return (
    <fieldset className="swarm-choices">
      <legend>{title}</legend>
      {options.length ? (
        options.map((option) => (
          <label key={option}>
            <input
              type="checkbox"
              checked={selected.includes(option)}
              onChange={(event) =>
                onChange(
                  event.target.checked
                    ? [...selected, option]
                    : selected.filter((value) => value !== option),
                )
              }
            />
            {option}
          </label>
        ))
      ) : (
        <span className="muted">No existing roles.</span>
      )}
    </fieldset>
  );
}

export function AddAgent({
  api,
  roles,
  instances,
  role,
  onCreated,
  onClose,
}: {
  api: BurnApi;
  roles: Role[];
  instances: Instance[];
  role?: Role;
  onCreated: (role: Role) => void;
  onClose: () => void;
}) {
  const [name, setName] = useState(role?.name ?? "");
  const [tools, setTools] = useState<string[]>([]);
  const [credentials, setCredentials] = useState<string[]>([]);
  const [mayMessage, setMayMessage] = useState<string[]>([]);
  const [maySpawn, setMaySpawn] = useState<string[]>([]);
  const [egress, setEgress] = useState("");
  const [owner, setOwner] = useState("");
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
    const hosts = egress.split(/[\s,]+/).filter(Boolean);
    if (hosts.some((host) => !/^(\*\.)?[a-z0-9.-]{1,253}$/.test(host))) {
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
          managed_by: "ui",
        };
        target = await api.createRole(spec);
        setCreated(target);
        onCreated(target);
      }
      const token = await api.createToken(target.name, {
        uses: tokenUses,
        ttl: tokenTtl,
      });
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

  const command = join?.token
    ? `burn run --role ${join.role} --token ${join.token} -- <your agent command>`
    : "";
  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopyStatus("Copied.");
    } catch {
      setCopyStatus("Copy unavailable. Select and copy the command above.");
    }
  }
  return (
    <SwarmDialog
      title={role ? `New token for ${role.name}` : "Add agent"}
      busy={busy}
      onClose={onClose}
    >
      {step === "form" ? (
        <form onSubmit={(event) => void submit(event)}>
          <fieldset
            disabled={busy || Boolean(created)}
            className="swarm-fields"
          >
            <label>
              Role name
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
                maxLength={32}
                autoComplete="off"
              />
            </label>
            <p className="muted">
              1–32 lowercase letters, digits or hyphens; start with a letter or
              digit.
            </p>
            {!role && (
              <>
                <Choices
                  title="Tools"
                  options={toolContract.tools.map((tool) => tool.name)}
                  selected={tools}
                  onChange={setTools}
                />
                <Choices
                  title="Credentials"
                  options={["api_key", "aws_key"]}
                  selected={credentials}
                  onChange={setCredentials}
                />
                <Choices
                  title="may_message"
                  options={roles.map((item) => item.name)}
                  selected={mayMessage}
                  onChange={setMayMessage}
                />
                <Choices
                  title="may_spawn"
                  options={roles.map((item) => item.name)}
                  selected={maySpawn}
                  onChange={setMaySpawn}
                />
                <label>
                  Egress hosts (optional)
                  <input
                    value={egress}
                    onChange={(event) => setEgress(event.target.value)}
                    placeholder="api.example.com, *.example.org"
                  />
                </label>
                <p className="muted">
                  Leave blank to use the default allowlist.
                </p>
                <label>
                  Owner
                  <input
                    value={owner}
                    onChange={(event) => setOwner(event.target.value)}
                    autoComplete="off"
                  />
                </label>
              </>
            )}
          </fieldset>
          {created && (
            <p>Role {created.name} exists. Create a new join token below.</p>
          )}
          <fieldset disabled={busy} className="swarm-fields">
            <legend>Join token</legend>
            <label>
              Uses
              <input
                type="number"
                min={1}
                max={10000}
                step={1}
                required
                value={uses}
                onChange={(event) => setUses(event.target.value)}
              />
            </label>
            <label>
              Lifetime (seconds)
              <input
                type="number"
                min={60}
                max={2592000}
                step={1}
                required
                value={ttl}
                onChange={(event) => setTtl(event.target.value)}
              />
            </label>
          </fieldset>
          <ErrorMessage message={error} />
          <button className="secondary-button" disabled={busy} type="submit">
            {busy
              ? "Creating…"
              : created
                ? "Create token"
                : "Create role and token"}
          </button>
        </form>
      ) : step === "command" ? (
        <div className="swarm-fields">
          <p>
            Copy this join command now. The token will not be shown again after
            you continue or close this window.
          </p>
          <pre className="swarm-command">
            <code>{command}</code>
          </pre>
          <button
            className="secondary-button"
            type="button"
            onClick={() => void copy()}
          >
            Copy command
          </button>
          <p role="status">{copyStatus}</p>
          <button
            className="secondary-button"
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
      ) : (
        <div className="swarm-fields" role="status">
          <p>
            {step === "connected"
              ? `Connected · ${join?.role}`
              : `Waiting for ${join?.role} to connect…`}
          </p>
          <p className="muted">
            {step === "connected"
              ? "The instance is now listed in Instances by role."
              : "Run the saved command. Checking instances every five seconds."}
          </p>
        </div>
      )}
    </SwarmDialog>
  );
}
