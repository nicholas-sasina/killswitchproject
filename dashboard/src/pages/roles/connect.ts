/**
 * What the Connect dialog says: the commands that connect an agent from
 * the harness someone already uses, what that agent gets, and which new
 * agent is the one they just started. Pure and tested.
 */
import type { Role } from "../../contracts";
import { joinExports, type JoinUrls } from "../../join";
import { displayName, type ConsoleAgent, type ConsoleModel } from "../../model";

export type Harness = "claude" | "opencode" | "codex" | "script";

export interface HarnessChoice {
  value: Harness;
  label: string;
  /** What follows `burn run`. */
  command: string;
  /** The role burn run names after the command when none is given. */
  group: string;
}

export const HARNESSES: readonly HarnessChoice[] = [
  { value: "claude", label: "Claude Code", command: "claude", group: "claude" },
  {
    value: "opencode",
    label: "OpenCode",
    command: "opencode",
    group: "opencode",
  },
  { value: "codex", label: "Codex", command: "codex", group: "codex" },
  {
    value: "script",
    label: "Script",
    command: "python agent.py",
    group: "agent",
  },
];

export function harnessOf(value: Harness): HarnessChoice {
  return HARNESSES.find((choice) => choice.value === value) ?? HARNESSES[0];
}

/** Installs the burn CLI. Shown as text; the console never fetches it. */
export const INSTALL_LINE =
  'uv tool install "git+https://github.com/nicholas-sasina/killswitchproject#subdirectory=cli"';

/** Once per machine: install burn, point it at this Burn, sign in. */
export function setupCommands(urls: JoinUrls): string {
  return `${INSTALL_LINE}\n${joinExports(urls)}burn login`;
}

/** `burn run claude`, or `burn run --role reviewer claude` with a role. */
export function runCommand(harness: Harness, role?: string): string {
  const { command } = harnessOf(harness);
  return role ? `burn run --role ${role} ${command}` : `burn run ${command}`;
}

/** The role the new agent joins: the one chosen, or the harness's own. */
export function targetRole(harness: Harness, role?: string): string {
  return role || harnessOf(harness).group;
}

/** The name the next agent of a role takes, such as `claude-01`. */
export function nextAgentName(model: ConsoleModel, role: string): string {
  const numbers = (model.roleByName.get(role)?.agents ?? []).map(
    (agent) => agent.number,
  );
  return displayName(role, Math.max(0, ...numbers) + 1);
}

/**
 * One plain sentence on what the agent gets. A burn kills the agent's own
 * AWS key, which is also how Claude Code and OpenCode reach their model;
 * Codex connects to Burn's tools but has no model under burn run yet.
 */
export function agentGets(
  harness: Harness,
  name: string,
  role?: Pick<Role, "credentials">,
): string {
  const start = `It gets its own identity, its own keys and a canary, and appears here as ${name}.`;
  const ownAws = role ? (role.credentials ?? []).includes("aws_key") : true;
  if (harness === "codex")
    return `${start} Codex connects to Burn's tools but brings no model yet.`;
  if (!ownAws) return start;
  return harness === "script"
    ? `${start} Burning it also cuts off any model it reaches with its own AWS key.`
    : `${start} Burning it also cuts off its model.`;
}

/**
 * The first agent of the role started after the dialog opened: not one
 * already listed then, and created no earlier than the open time, less
 * a minute for clocks that disagree.
 */
export function firstNewAgent(
  agents: readonly ConsoleAgent[],
  role: string,
  openedAt: number,
  known: ReadonlySet<string>,
): ConsoleAgent | undefined {
  return agents.find(
    (agent) =>
      agent.role === role &&
      !known.has(agent.key) &&
      Date.parse(agent.createdAt ?? "") >= openedAt - 60_000,
  );
}
