/**
 * Keys by name. The API names a credential by its reference
 * (`burn/shared/crm_db_url`, `burn/agent-worker-1f/api_key`); the console
 * shows the last part in mono.
 */

/** "burn/shared/crm_db_url" is "crm_db_url". */
export function keyName(ref: string | null | undefined): string {
  if (!ref) return "";
  const parts = ref.split("/").filter(Boolean);
  return parts.at(-1) ?? ref;
}

/** A session's canary: a decoy key that exists only to be stolen. */
export function isCanaryRef(ref: string | null | undefined): boolean {
  return Boolean(ref && /(^|[/_-])canary/i.test(ref));
}
