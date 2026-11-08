/**
 * How the console writes times. Everything is the viewer's local time, in
 * these forms: "7:42:31" in the Live feed, "7:42:09 PM" on an
 * agent's timeline, "7:42 PM" in a sentence, "Nov 6, 3:15 PM" for another day.
 * Each function returns "" for a missing or unreadable time, so a caller can
 * leave the phrase out rather than print "Invalid Date".
 */

type When = string | number | Date | null | undefined;

function toDate(value: When): Date | undefined {
  if (value === null || value === undefined || value === "") return undefined;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.valueOf()) ? undefined : date;
}

const LOCALE = "en-US";

/** "7:42:31": the Live feed's mono times, with no AM or PM. */
export function clock(value: When): string {
  const date = toDate(value);
  if (!date) return "";
  const hours = date.getHours() % 12 || 12;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${hours}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/** "7:42:09 PM": a timeline entry, or the moment something was contained. */
export function clockTime(value: When): string {
  const date = toDate(value);
  return date
    ? date.toLocaleTimeString(LOCALE, {
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
      })
    : "";
}

/** "7:42 PM". */
export function shortTime(value: When): string {
  const date = toDate(value);
  return date
    ? date.toLocaleTimeString(LOCALE, { hour: "numeric", minute: "2-digit" })
    : "";
}

export function startOfDay(now: Date): Date {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

/** Whether a time falls on the same local calendar day as `now`. */
export function isSameDay(value: When, now: Date): boolean {
  const date = toDate(value);
  return Boolean(
    date &&
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate(),
  );
}

/** "7:42 PM" today, "Nov 6, 3:15 PM" another day. */
export function dayTime(value: When, now: Date): string {
  const date = toDate(value);
  if (!date) return "";
  if (isSameDay(date, now)) return shortTime(date);
  const day = date.toLocaleDateString(LOCALE, {
    month: "short",
    day: "numeric",
    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
  return `${day}, ${shortTime(date)}`;
}

/** "2 s ago", "4 min ago", "3 h ago"; older than a day, `dayTime`. */
export function ago(value: When, now: Date): string {
  const date = toDate(value);
  if (!date) return "";
  const seconds = Math.max(
    0,
    Math.round((now.valueOf() - date.valueOf()) / 1000),
  );
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)} h ago`;
  return dayTime(date, now);
}

/** "30 minutes", "2 hours", "3 days": how long since a time. */
export function duration(value: When, now: Date): string {
  const date = toDate(value);
  if (!date) return "";
  const minutes = Math.max(
    0,
    Math.floor((now.valueOf() - date.valueOf()) / 60_000),
  );
  const unit = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
  if (minutes < 1) return "less than a minute";
  if (minutes < 60) return unit(minutes, "minute");
  if (minutes < 60 * 24) return unit(Math.floor(minutes / 60), "hour");
  return unit(Math.floor(minutes / (60 * 24)), "day");
}

/** The machine-readable form for a <time dateTime>. */
export function isoTime(value: When): string | undefined {
  return toDate(value)?.toISOString();
}
