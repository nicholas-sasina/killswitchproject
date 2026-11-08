/**
 * The commands that connect an agent with a join token. The token is never
 * in them: it is pasted at a hidden prompt or piped from the clipboard. They
 * set what burn run needs besides the token, the API and gateway URLs, so
 * someone given only the dashboard can connect an agent.
 */

/** The CLI's own instructions for installing burn and connecting an agent. */
export const CONNECT_GUIDE_URL =
  "https://github.com/nicholas-sasina/killswitchproject/blob/main/cli/README.md#connect-your-agent";

export interface JoinUrls {
  /** The Burn API the dashboard itself uses. */
  api?: string;
  /** The AgentCore Gateway's MCP endpoint. */
  gateway?: string;
}

// http(s), a host, an optional port and a plain path: nothing a shell reads
// as anything but text, so the URL pastes unquoted.
const PASTEABLE_URL =
  /^https?:\/\/[A-Za-z0-9.-]+(:[0-9]{1,5})?(\/[A-Za-z0-9._~%/-]*)?$/;

/** The URL without a trailing slash, or "" when it is missing or would not paste as is. */
function pasteable(url: string | undefined): string {
  const value = (url ?? "").trim().replace(/\/+$/, "");
  return PASTEABLE_URL.test(value) ? value : "";
}

export function joinCommands(role: string, urls: JoinUrls = {}) {
  // A missing URL leaves its line out rather than show a broken one.
  const exports = (
    [
      ["BURN_API_URL", pasteable(urls.api)],
      ["BURN_GATEWAY_URL", pasteable(urls.gateway)],
    ] as const
  )
    .filter(([, url]) => url)
    .map(([name, url]) => `export ${name}=${url}\n`)
    .join("");
  return {
    prompt: `${exports}read -rs BURN_JOIN_TOKEN && export BURN_JOIN_TOKEN   # paste the token, press Enter\nburn run --role ${role} -- <your agent command>`,
    clipboard: `${exports}pbpaste | burn run --role ${role} --token-stdin -- <your agent command>`,
  };
}
