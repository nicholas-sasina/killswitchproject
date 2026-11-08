import { describe, expect, it } from "vitest";
import { isCanaryRef, keyName } from "./keys";
import { initials, personLabel } from "./people";
import { regionFromUrl } from "./region";

describe("keys, people and the region", () => {
  it("names a key by the last part of its reference", () => {
    expect(keyName("burn/shared/crm_db_url")).toBe("crm_db_url");
    expect(keyName(null)).toBe("");
    expect(isCanaryRef("burn/agent-worker-1/canary_1")).toBe(true);
    expect(isCanaryRef("burn/shared/crm_db_url")).toBe(false);
  });

  it("shows people as their email, and Burn as Burn", () => {
    expect(personLabel("priya@example.test")).toBe("priya@example.test");
    expect(personLabel("system")).toBe("Burn");
    expect(personLabel(null)).toBe("");
    // An agent acting with its own badge is not a person.
    expect(personLabel("instance:session_0123")).toBe("");
    expect(initials("priya.nair@example.test")).toBe("PN");
    expect(initials("dana@example.test")).toBe("DA");
    expect(initials(undefined)).toBe("?");
  });

  it("reads the region from the API's URL", () => {
    expect(
      regionFromUrl("https://abc123.execute-api.us-east-1.amazonaws.com/prod"),
    ).toBe("us-east-1");
    expect(regionFromUrl("https://x.lambda-url.eu-west-2.on.aws/")).toBe(
      "eu-west-2",
    );
    expect(regionFromUrl("http://localhost:8700")).toBeUndefined();
  });
});
