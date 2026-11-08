import { describe, expect, it } from "vitest";
import type { Instance } from "../../contracts";
import { mergeFleet } from "../../fleet";
import { buildConsole } from "../../model";
import { FULL_ACCESS_TOOLS } from "./model";
import {
  HARNESSES,
  agentGets,
  firstNewAgent,
  nextAgentName,
  noRoleLabel,
  runCommand,
  setupCommands,
  targetRole,
} from "./connect";

const opened = Date.parse("2026-11-08T11:00:00Z");

function instance(session: string, role: string, created: string): Instance {
  return {
    session_id: session,
    agent_id: `agent-${role}-${session}`,
    role,
    status: "active",
    created_at: created,
  };
}

const model = buildConsole({
  fleet: mergeFleet(
    [],
    [
      instance("c1", "claude", "2026-11-08T10:00:00Z"),
      instance("c2", "claude", "2026-11-08T10:30:00Z"),
      instance("c3", "claude", "2026-11-08T11:00:05Z"),
      instance("r1", "reviewer", "2026-11-08T11:00:06Z"),
    ],
  ),
  events: [],
  now: new Date(opened + 60_000),
});

describe("the Connect dialog's commands", () => {
  it("installs burn, points it at this Burn and signs in, once per machine", () => {
    expect(
      setupCommands({
        api: "https://api.example.test/",
        gateway: "https://gw.example.test/mcp",
      }),
    ).toBe(
      'uv tool install "git+https://github.com/nicholas-sasina/killswitchproject#subdirectory=cli"\n' +
        "export BURN_API_URL=https://api.example.test\n" +
        "export BURN_GATEWAY_URL=https://gw.example.test/mcp\n" +
        "burn login",
    );
    expect(setupCommands({})).not.toContain("export");
  });

  it("sets the people sign-in burn login needs, from the console's own", () => {
    expect(
      setupCommands(
        { api: "https://api.example.test" },
        {
          domain: "burn-people.auth.us-east-1.amazoncognito.com",
          clientId: "4abc5def6ghi7jkl8mno9pqr0s",
        },
      ),
    ).toBe(
      'uv tool install "git+https://github.com/nicholas-sasina/killswitchproject#subdirectory=cli"\n' +
        "export BURN_API_URL=https://api.example.test\n" +
        "export BURN_PEOPLE_ENABLED=on\n" +
        "export BURN_PEOPLE_DOMAIN=burn-people.auth.us-east-1.amazoncognito.com\n" +
        "export BURN_PEOPLE_CLIENT_ID=4abc5def6ghi7jkl8mno9pqr0s\n" +
        "burn login",
    );
    expect(
      setupCommands({}, { domain: "https://login.example.test:8443/" }),
    ).toContain("export BURN_PEOPLE_DOMAIN=https://login.example.test:8443\n");
  });

  it("turns people sign-in on only with both values, and never for a local build", () => {
    const enabled = (people: Parameters<typeof setupCommands>[1]) =>
      setupCommands({}, people).includes("export BURN_PEOPLE_ENABLED=on\n");
    expect(enabled({ domain: "login.example.test", clientId: "abc123" })).toBe(
      true,
    );
    expect(enabled({ domain: "login.example.test" })).toBe(false);
    expect(enabled({ clientId: "abc123" })).toBe(false);
    expect(
      enabled({
        domain: "login.example.test",
        clientId: "abc123",
        localDev: true,
      }),
    ).toBe(false);
  });

  it("leaves a sign-in line out when its value is missing or would not paste", () => {
    const lines = (domain?: string, clientId?: string) =>
      setupCommands({}, { domain, clientId })
        .split("\n")
        .filter((line) => line.startsWith("export"));
    expect(lines()).toEqual([]);
    expect(lines("login.example.test")).toEqual([
      "export BURN_PEOPLE_DOMAIN=login.example.test",
    ]);
    expect(lines("login.example.test", "abc $(id)")).toEqual([
      "export BURN_PEOPLE_DOMAIN=login.example.test",
    ]);
    expect(lines(undefined, "abc123")).toEqual([
      "export BURN_PEOPLE_CLIENT_ID=abc123",
    ]);
    expect(lines("login.example.test; rm -rf ~", "abc $(id)")).toEqual([]);
    expect(lines("http://login.example.test")).toEqual([]);
  });

  it("offers only the harnesses burn run connects with one command", () => {
    expect(HARNESSES.map((choice) => choice.label)).toEqual([
      "Claude Code",
      "Script",
    ]);
  });

  it("runs each harness, adding --role only when one is chosen", () => {
    expect(runCommand("claude")).toBe("burn run claude");
    expect(runCommand("script")).toBe("burn run python agent.py");
    expect(runCommand("claude", "reviewer")).toBe(
      "burn run --role reviewer claude",
    );
  });

  it("groups an agent under its harness unless a role is chosen", () => {
    expect(targetRole("claude")).toBe("claude");
    expect(targetRole("script")).toBe("agent");
    expect(targetRole("script", "reviewer")).toBe("reviewer");
  });

  it("says what the role burn run names after the harness is", () => {
    expect(noRoleLabel("claude")).toBe(
      "None (grouped as claude, Full access, new)",
    );
    expect(
      noRoleLabel("claude", { tools: [...FULL_ACCESS_TOOLS], egress: null }),
    ).toBe("None (grouped as claude, Full access)");
    expect(noRoleLabel("claude", { tools: ["read_ticket"] })).toBe(
      "None (grouped as claude, Limited)",
    );
  });
});

describe("what the agent gets", () => {
  it("names the next agent of the role", () => {
    expect(nextAgentName(model, "claude")).toBe("claude-04");
    expect(nextAgentName(model, "agent")).toBe("agent-01");
  });

  it("says a burn also revokes the AWS key its model uses", () => {
    expect(agentGets("claude", "claude-01")).toBe(
      "It gets its own identity, its own keys and a canary, and appears here as claude-01. Burning it also revokes its AWS key, which its model uses.",
    );
    expect(agentGets("script", "agent-01")).toContain(
      "revokes its AWS key, and any model it reaches with that key",
    );
  });

  it("promises no model cut-off for a role without its own AWS key", () => {
    expect(
      agentGets("claude", "reviewer-01", { credentials: ["api_key"] }),
    ).not.toContain("model");
  });
});

describe("firstNewAgent", () => {
  const known = new Set(["c1", "c2"]);

  it("finds the role's first agent started after the dialog opened", () => {
    expect(firstNewAgent(model.agents, "claude", opened, known)?.name).toBe(
      "claude-03",
    );
    expect(firstNewAgent(model.agents, "reviewer", opened, known)?.name).toBe(
      "reviewer-01",
    );
  });

  it("ignores agents already listed or started well before it opened", () => {
    expect(
      firstNewAgent(
        model.agents,
        "claude",
        opened,
        new Set(["c1", "c2", "c3"]),
      ),
    ).toBeUndefined();
    expect(
      firstNewAgent(model.agents, "claude", opened + 3_600_000, new Set()),
    ).toBeUndefined();
    expect(firstNewAgent(model.agents, "agent", opened, known)).toBeUndefined();
  });
});
