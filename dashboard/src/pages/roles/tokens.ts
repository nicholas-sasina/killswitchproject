/**
 * Join tokens this tab made or revoked. The API has no route that lists a
 * role's tokens, so the role page shows uses left, expiry and creator only
 * for the tokens whose records it was handed: a create or a revoke reply.
 *
 * Only the record is kept, never the token itself: `tokenRecord` copies the
 * named fields and nothing else, so the secret a create reply carries can
 * never reach this store. It lives in memory, for this tab only.
 */
import { useSyncExternalStore } from "react";
import type { EnrollToken } from "../../contracts";

let records: readonly EnrollToken[] = [];
const listeners = new Set<() => void>();

/** A token's record without its secret, whatever the reply carried. */
export function tokenRecord(reply: EnrollToken): EnrollToken {
  return {
    token_id: reply.token_id,
    role: reply.role,
    uses: reply.uses,
    uses_left: reply.uses_left,
    expires_at: reply.expires_at,
    status: reply.status,
    created_at: reply.created_at,
    created_by: reply.created_by,
  };
}

/** Keep the newest record of a token. */
export function rememberToken(reply: EnrollToken): void {
  const record = tokenRecord(reply);
  records = [
    ...records.filter((known) => known.token_id !== record.token_id),
    record,
  ];
  for (const listener of listeners) listener();
}

/** Every record this tab holds. */
export function knownTokens(): readonly EnrollToken[] {
  return records;
}

/** For tests: start empty. */
export function forgetTokens(): void {
  records = [];
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The records, re-rendering when one is added. */
export function useKnownTokens(): readonly EnrollToken[] {
  return useSyncExternalStore(subscribe, knownTokens, knownTokens);
}
