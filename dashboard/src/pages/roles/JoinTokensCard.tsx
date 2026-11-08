import { useMemo, useState } from "react";
import { useConsole } from "../../data/ConsoleData";
import type { ConsoleAgent } from "../../model";
import { Card, CardNote, EmptyState, Notice } from "../../ui";
import { TokenDialog } from "./TokenDialog";
import {
  rolePlural,
  roleTokens,
  shortToken,
  tokenLine,
  tokenOpen,
} from "./model";
import { rememberToken, useKnownTokens } from "./tokens";

/**
 * "Join tokens": the tokens this role's agents joined with, and the ones
 * made or revoked here, with Revoke and New token. A role is reusable, and
 * each token has its own uses and expiry. The API can't list a role's
 * tokens, so a token's uses and expiry show only once this tab has its
 * record.
 */
export function JoinTokensCard({
  role,
  agents,
  canAdd,
}: {
  role: string;
  agents: ConsoleAgent[];
  /** Whether the role takes join tokens at all. */
  canAdd: boolean;
}) {
  const { api, model } = useConsole();
  const [minting, setMinting] = useState(false);
  const known = useKnownTokens();
  const rows = useMemo(
    () => roleTokens(agents, [...known], role),
    [agents, known, role],
  );
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState("");
  const plural = rolePlural(role);

  async function revoke(tokenId: string) {
    if (busy) return;
    setBusy(tokenId);
    setError("");
    try {
      rememberToken(await api.revokeToken(tokenId));
    } catch (caught) {
      setError(
        `Token ${shortToken(tokenId)} was not revoked. ${
          caught instanceof Error ? caught.message : "The request failed."
        } Try again.`,
      );
    } finally {
      setBusy(undefined);
    }
  }

  return (
    <Card
      title="Join tokens"
      pad="narrow"
      className="role-tokens"
      aside={
        canAdd ? (
          <button
            className="btn btn-sm"
            type="button"
            onClick={() => setMinting(true)}
          >
            New token
          </button>
        ) : undefined
      }
    >
      {canAdd && (
        <p className="role-token-reuse">
          A role is reusable: connect as many agents as you like. Each join
          token has its own uses and expiry.
        </p>
      )}
      {rows.length ? (
        <ul className="role-token-list">
          {rows.map((row) => {
            const short = shortToken(row.tokenId);
            return (
              <li key={row.tokenId} className="role-token">
                <div>
                  <span className="id role-token-id">{short}</span>
                  <span className="role-token-line">
                    {tokenLine(row, role, model.now)}
                  </span>
                </div>
                {tokenOpen(row, model.now) && (
                  <button
                    className="btn btn-sm"
                    type="button"
                    disabled={busy === row.tokenId}
                    aria-label={`Revoke token ${short}`}
                    onClick={() => void revoke(row.tokenId)}
                  >
                    {busy === row.tokenId ? "Revoking…" : "Revoke"}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <EmptyState>
          {canAdd
            ? "No join tokens used yet. New token makes one."
            : "This role's agent is started by the scenario, without a join token."}
        </EmptyState>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      {rows.length > 0 && (
        <CardNote>
          Revoking a token stops new {plural} joining with it.{" "}
          {plural[0].toUpperCase()}
          {plural.slice(1)} that already joined keep running; burn them from the
          table.
        </CardNote>
      )}
      {minting && <TokenDialog role={role} onClose={() => setMinting(false)} />}
    </Card>
  );
}
