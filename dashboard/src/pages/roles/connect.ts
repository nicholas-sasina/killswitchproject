/**
 * What the Connect dialog says: the commands that connect an agent from
 * the harness someone already uses, what that agent gets, and which new
 * agent is the one they just started. Pure and tested.
 */
import type { Role } from "../../contracts";
import { accessLevel } from "./model";
import { joinExports, type JoinUrls } from "../../join";
import { displayName, type ConsoleAgent, type ConsoleModel } from "../../model";

/**
 * The harnesses `burn run` connects with one command: Claude Code, which
 * burn run sets up with Burn's tools and its own AWS key for its model, and
 * a script, which reads the instance's MCP URL and token from its
 * environment. Other harnesses need their own setup; the CLI's guide has it.
 */
export type Harness = "claude" | "script";

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

/** The console's own people sign-in, which burn login uses too. Public values. */
export interface PeopleSignIn {
  /** The managed-login host or https origin. */
  domain?: string;
  clientId?: string;
  /** A local build signs in with a demo token, not people sign-in. */
  localDev?: boolean;
}

// A host or an https origin, and an app client id: nothing a shell reads as
// anything but text, so each pastes unquoted. Anything else is left out.
const PASTEABLE_DOMAIN =
  /^(https:\/\/[A-Za-z0-9.-]+(:[0-9]{1,5})?|[A-Za-z0-9.-]+)$/;
const PASTEABLE_CLIENT_ID = /^[A-Za-z0-9_-]{1,128}$/;

/**
 * The people sign-in lines. With both values, BURN_PEOPLE_ENABLED=on too:
 * without it burn asks AWS whether the stack requires sign-in, and with no
 * AWS access it assumes not and wants a demo token instead.
 */
function signInExports({ domain, clientId, localDev }: PeopleSignIn): string {
  const host = (domain ?? "").trim().replace(/\/+$/, "");
  const client = (clientId ?? "").trim();
  const shownHost = PASTEABLE_DOMAIN.test(host) ? host : "";
  const shownClient = PASTEABLE_CLIENT_ID.test(client) ? client : "";
  const enabled = shownHost && shownClient && !localDev ? "on" : "";
  return (
    [
      ["BURN_PEOPLE_ENABLED", enabled],
      ["BURN_PEOPLE_DOMAIN", shownHost],
      ["BURN_PEOPLE_CLIENT_ID", shownClient],
    ] as const
  )
    .filter(([, value]) => value)
    .map(([name, value]) => `export ${name}=${value}\n`)
    .join("");
}

/**
 * Once per machine: install burn, point it at this Burn and its sign-in,
 * then sign in. burn login needs the people domain and client id; a value
 * the console does not have leaves its line out.
 */
export function setupCommands(
  urls: JoinUrls,
  people: PeopleSignIn = {},
): string {
  return `${INSTALL_LINE}\n${joinExports(urls)}${signInExports(people)}burn login`;
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
 * One plain sentence on what the agent gets. A burn revokes the agent's
 * own AWS key, the key its model uses; a role without aws_key gives it none,
 * and then nothing is said about a model.
 */
export function agentGets(
  harness: Harness,
  name: string,
  role?: Pick<Role, "credentials">,
): string {
  const start = `It gets its own identity, its own keys and a canary, and appears here as ${name}.`;
  const ownAws = role ? (role.credentials ?? []).includes("aws_key") : true;
  if (!ownAws) return start;
  return harness === "script"
    ? `${start} Burning it also revokes its AWS key, and any model it reaches with that key.`
    : `${start} Burning it also revokes its AWS key, which its model uses.`;
}

/**
 * The Role select's first option: no role, so burn run groups the agent
 * under the harness's name. That role is used as it is when it exists, so
 * say what it is; otherwise burn run creates it with Full access.
 */
export function noRoleLabel(
  group: string,
  existing?: Pick<Role, "tools" | "egress">,
): string {
  return existing
    ? `None (grouped as ${group}, ${accessLevel(existing)})`
    : `None (grouped as ${group}, Full access, new)`;
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
