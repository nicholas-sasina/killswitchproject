import { describe, expect, it } from "vitest";
import {
  ago,
  clock,
  clockTime,
  dayTime,
  duration,
  isSameDay,
  shortTime,
} from "./time";

const now = new Date(2026, 10, 8, 19, 45, 0);
const t = new Date(2026, 10, 8, 19, 42, 9);

describe("console times", () => {
  it("writes each form the console uses", () => {
    expect(clock(t)).toBe("7:42:09");
    expect(clockTime(t)).toBe("7:42:09 PM");
    expect(shortTime(t)).toBe("7:42 PM");
    expect(dayTime(t, now)).toBe("7:42 PM");
    expect(dayTime(new Date(2026, 10, 6, 15, 15), now)).toBe("Nov 6, 3:15 PM");
    expect(dayTime(new Date(2025, 10, 6, 15, 15), now)).toBe(
      "Nov 6, 2025, 3:15 PM",
    );
  });

  it("says how long ago and for how long", () => {
    expect(ago(new Date(2026, 10, 8, 19, 44, 58), now)).toBe("2 s ago");
    expect(ago(new Date(2026, 10, 8, 19, 41), now)).toBe("4 min ago");
    expect(ago(new Date(2026, 10, 8, 16, 40), now)).toBe("3 h ago");
    expect(duration(new Date(2026, 10, 8, 19, 15), now)).toBe("30 minutes");
    expect(duration(new Date(2026, 10, 8, 18, 44), now)).toBe("1 hour");
  });

  it("scopes today to the local calendar day", () => {
    expect(isSameDay(t.toISOString(), now)).toBe(true);
    expect(isSameDay(new Date(2026, 10, 7, 23, 59), now)).toBe(false);
  });

  it("returns nothing for a missing or unreadable time", () => {
    expect(clock(undefined)).toBe("");
    expect(clockTime("not a time")).toBe("");
    expect(ago(null, now)).toBe("");
    expect(isSameDay(undefined, now)).toBe(false);
  });
});
