import { Card, Pill } from "../../ui";
import type { BurnedRow } from "./overview";

/** At most this many burns; Incidents has every one. */
const SHOWN = 6;

/** "Burned today": each burn, why, and what is still left to do. */
export function BurnedToday({ rows }: { rows: BurnedRow[] }) {
  return (
    <Card
      title="Burned today"
      sub={String(rows.length)}
      pad="narrow"
      className="ov-burned"
      aside={
        <a className="text-link" href="#/incidents">
          All incidents
        </a>
      }
    >
      {rows.length ? (
        <ul>
          {rows.slice(0, SHOWN).map((row) => (
            <li key={row.key}>
              <a className="ov-burned-row" href={row.href}>
                <span
                  className="square square-burned square-14"
                  aria-hidden="true"
                />
                <span className="ov-burned-text">
                  <span className="ov-burned-name id">{row.name}</span>
                  <span className="ov-burned-why">{row.why}</span>
                </span>
                <Pill tone={row.pillTone}>{row.pill}</Pill>
              </a>
            </li>
          ))}
        </ul>
      ) : (
        <p className="ov-empty">No agent has been burned today.</p>
      )}
    </Card>
  );
}
