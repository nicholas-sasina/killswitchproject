import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { FULL_ACCESS_TOOLS, fullAccessRole } from "./model";

// Read at test time from the repository's contract, which the API and the
// CLI build against too.
const contract = JSON.parse(
  readFileSync(
    fileURLToPath(
      new URL("../../../../contracts/mcp-tools.json", import.meta.url),
    ),
    "utf8",
  ),
);

describe("Full access", () => {
  it("lists every tool in contracts/mcp-tools.json", () => {
    expect([...FULL_ACCESS_TOOLS]).toEqual(
      contract.tools.map((tool) => tool.name),
    );
  });

  it("creates exactly the shared Full access body", () => {
    expect(fullAccessRole("reviewer")).toStrictEqual({
      name: "reviewer",
      tools: [
        "list_tickets",
        "read_ticket",
        "read_config",
        "query_customers",
        "send_message",
        "read_inbox",
        "notify",
      ],
      credentials: ["aws_key"],
      may_message: [],
      may_spawn: [],
      egress: null,
      owner: null,
      canaries: true,
      auto_burn: true,
      replace_on_burn: false,
    });
  });
});
