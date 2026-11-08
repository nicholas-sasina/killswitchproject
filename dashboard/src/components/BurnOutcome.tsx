import type { BurnResult } from "../contracts";

export function BurnOutcome({
  result,
  compact = false,
}: {
  result: BurnResult;
  compact?: boolean;
}) {
  if (compact)
    return (
      <div
        className="burn-summary"
        role="status"
        aria-label={`Burn summary for ${result.agent_id}`}
      >
        <strong>
          {result.revoked ? "Contained" : "Revocation unconfirmed"}
        </strong>
        <span>
          Rotated <b>{result.rotated.length}</b> · Flagged{" "}
          <b>{result.flagged.length}</b>
        </span>
      </div>
    );
  return (
    <section className="burn-outcome" aria-label="Burn result" role="status">
      <div>
        <span className="eyebrow">Containment result</span>
        <h2>
          {result.agent_id} ·{" "}
          {result.revoked ? "revoked" : "revocation not confirmed"}
        </h2>
        <p>
          {result.sessions_revoked.length}{" "}
          {result.sessions_revoked.length === 1 ? "session" : "sessions"}{" "}
          revoked
        </p>
      </div>
      <div>
        <h3>Rotated · {result.rotated.length}</h3>
        {result.rotated.length ? (
          <ul>
            {result.rotated.map((ref) => (
              <li key={ref}>
                <code>{ref}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p>No credentials rotated.</p>
        )}
      </div>
      <div>
        <h3>Flagged for review · {result.flagged.length}</h3>
        {result.flagged.length ? (
          <ul>
            {result.flagged.map((ref) => (
              <li key={ref}>
                <code>{ref}</code>
              </li>
            ))}
          </ul>
        ) : (
          <p>No credentials flagged.</p>
        )}
      </div>
    </section>
  );
}
