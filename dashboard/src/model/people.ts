/**
 * People in the console. The API records people by email (`created_by`,
 * `actor`, a flag's `by`) and has no names, so a person is shown as their
 * email. Burn's own actions carry the actor "system", and an agent acting
 * with its own badge carries "instance:<session_id>", which is not a person.
 */

/** How to write who did something, or "" when nobody is recorded. */
export function personLabel(who: string | null | undefined): string {
  const value = who?.trim() ?? "";
  if (!value) return "";
  if (value === "system") return "Burn";
  if (value === "admin") return "the local admin";
  if (value.startsWith("instance:")) return "";
  return value;
}

/** Two letters for the signed-in person's badge: "priya.nair@…" is "PN". */
export function initials(who: string | null | undefined): string {
  const local = (who ?? "").split("@")[0].trim();
  if (!local) return "?";
  const parts = local.split(/[._\-+\s]+/).filter(Boolean);
  const letters =
    parts.length > 1 ? parts[0][0] + parts[1][0] : local.slice(0, 2);
  return letters.toUpperCase();
}
