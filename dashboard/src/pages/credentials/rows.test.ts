import { describe, expect, it } from "vitest";
import { event, flag, today } from "../../features/flags/testConsole";
import {
  credentialCounts,
  credentialRows,
  decisionLines,
  inFilter,
} from "./rows";

const rotation = (ref: string, at: string, detail?: string) =>
  event({
    action: "rotated",
    agent_id: "system",
    secret_ref: ref,
    ts: at,
    actor: "priya.nair@example.com",
    detail,
  });

describe("credential rows", () => {
  it("puts open keys first, highest level first, then decisions newest first", () => {
    const rows = credentialRows(
      [
        flag({ secret_ref: "burn/shared/a", flag_level: "seen" }),
        flag({ secret_ref: "burn/shared/b", flag_level: "reported" }),
        flag({
          secret_ref: "burn/shared/c",
          state: "dismissed",
          resolution: {
            action: "dismiss",
            reason: "test key",
            at: today(8, 0),
            level: "seen",
          },
        }),
      ],
      [
        rotation("burn/shared/d", today(9, 0)),
        // A key GET /flags still lists is shown from its flag, not this.
        rotation("burn/shared/a", today(9, 30)),
      ].reverse(),
    );
    expect(rows.map((row) => [row.key, row.state])).toEqual([
      ["b", "open"],
      ["a", "open"],
      ["d", "rotated"],
      ["c", "dismissed"],
    ]);
    expect(credentialCounts(rows)).toEqual({ open: 2, resolved: 2, all: 4 });
    expect(rows.filter((row) => inFilter(row, "resolved"))).toHaveLength(2);
    expect(rows.filter((row) => inFilter(row, "open"))).toHaveLength(2);
    expect(rows.filter((row) => inFilter(row, "all"))).toHaveLength(4);
  });

  it("says who decided, why, when, and what reopens a dismissed flag", () => {
    const now = new Date(today(20, 0));
    const [dismissed] = credentialRows(
      [
        flag({
          state: "dismissed",
          flag_level: "attempted",
          resolution: {
            action: "dismiss",
            reason: "rotated by hand in the vault",
            by: "priya.nair@example.com",
            at: today(19, 0),
            level: "attempted",
          },
        }),
      ],
      [],
    );
    const lines = decisionLines(dismissed, now);
    expect(lines?.title).toBe("Dismissed by priya.nair@example.com");
    expect(lines?.detail).toMatch(/^“rotated by hand in the vault”, 7:00\sPM$/);
    expect(lines?.note).toBe(
      "It reopens only if evidence at reported arrives.",
    );

    const [rotated] = credentialRows(
      [],
      [
        rotation(
          "burn/shared/crm_db_url",
          today(18, 30),
          "priya.nair@example.com rotated this flagged credential (was reported, 2 instance(s)): leaked in a ticket",
        ),
      ],
    );
    expect(rotated.level).toBe("reported");
    expect(decisionLines(rotated, now)).toEqual({
      title: "Rotated by priya.nair@example.com",
      detail: expect.stringMatching(/^“leaked in a ticket”, 6:30\sPM$/),
    });
    expect(decisionLines(credentialRows([flag()], [])[0], now)).toBeUndefined();
  });
});
