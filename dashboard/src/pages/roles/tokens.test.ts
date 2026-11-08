import { afterEach, describe, expect, it } from "vitest";
import type { EnrollToken } from "../../contracts";
import {
  forgetTokens,
  knownTokens,
  rememberToken,
  tokenRecord,
} from "./tokens";

const reply = {
  token_id: "abc",
  role: "worker",
  uses: 2,
  uses_left: 2,
  expires_at: "2026-11-08T21:00:00Z",
  status: "active",
  created_at: "2026-11-08T20:00:00Z",
  created_by: "p@x.test",
  token: "bjt_abc.secret-value",
} as EnrollToken & { token: string };

afterEach(() => forgetTokens());

describe("join token records", () => {
  it("keeps the record and never the token itself", () => {
    expect(JSON.stringify(tokenRecord(reply))).not.toContain("secret-value");
    rememberToken(reply);
    expect(JSON.stringify(knownTokens())).not.toContain("secret-value");
    expect(knownTokens()).toEqual([
      {
        token_id: "abc",
        role: "worker",
        uses: 2,
        uses_left: 2,
        expires_at: "2026-11-08T21:00:00Z",
        status: "active",
        created_at: "2026-11-08T20:00:00Z",
        created_by: "p@x.test",
      },
    ]);
  });

  it("keeps the newest record of a token", () => {
    rememberToken(reply);
    rememberToken({ ...reply, status: "revoked" });
    expect(knownTokens().map((token) => token.status)).toEqual(["revoked"]);
  });
});
