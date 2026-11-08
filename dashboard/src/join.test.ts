import { describe, expect, it } from "vitest";
import { CONNECT_GUIDE_URL, joinCommands } from "./join";

const API = "https://abc123.execute-api.us-east-1.amazonaws.com";
const GATEWAY =
  "https://gw-1.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp";
const PROMPT =
  "read -rs BURN_JOIN_TOKEN && export BURN_JOIN_TOKEN   # paste the token, press Enter\nburn run --role worker -- <your agent command>";
const CLIPBOARD =
  "pbpaste | burn run --role worker --token-stdin -- <your agent command>";

describe("join commands", () => {
  it("export both URLs burn run needs before it runs", () => {
    const exports = `export BURN_API_URL=${API}\nexport BURN_GATEWAY_URL=${GATEWAY}\n`;
    expect(joinCommands("worker", { api: API, gateway: GATEWAY })).toEqual({
      prompt: exports + PROMPT,
      clipboard: exports + CLIPBOARD,
    });
  });

  it("leave the gateway line out when the gateway URL is not configured", () => {
    for (const gateway of [undefined, "", "  "]) {
      const commands = joinCommands("worker", { api: API, gateway });
      expect(commands).toEqual({
        prompt: `export BURN_API_URL=${API}\n${PROMPT}`,
        clipboard: `export BURN_API_URL=${API}\n${CLIPBOARD}`,
      });
    }
  });

  it("show only URLs that paste into a shell as they are", () => {
    for (const broken of [
      "not a url",
      "ftp://gw.example.test/mcp",
      "https://gw.example.test/mcp $(id)",
      "https://gw.example.test/mcp;id",
      "https://gw.example.test/`id`",
      "https://gw.example.test/mcp?a=1&b=2",
    ]) {
      const { prompt, clipboard } = joinCommands("worker", {
        api: broken,
        gateway: broken,
      });
      expect(prompt).toBe(PROMPT);
      expect(clipboard).toBe(CLIPBOARD);
    }
    expect(
      joinCommands("worker", {
        api: "http://localhost:8700/",
        gateway: "http://localhost:8701/mcp",
      }).prompt,
    ).toBe(
      `export BURN_API_URL=http://localhost:8700\nexport BURN_GATEWAY_URL=http://localhost:8701/mcp\n${PROMPT}`,
    );
  });

  it("link to the CLI's Connect your agent section", () => {
    expect(CONNECT_GUIDE_URL).toBe(
      "https://github.com/nicholas-sasina/killswitchproject/blob/main/cli/README.md#connect-your-agent",
    );
  });
});
