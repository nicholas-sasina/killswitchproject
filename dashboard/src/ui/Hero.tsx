import { type ReactNode } from "react";
import { DigitRoll } from "./DigitRoll";
import "./ui.css";

export interface Crumb {
  label: string;
  href?: string;
}

export interface HeroStat {
  label: string;
  value: number;
  /** Ember for a count of burned agents above zero. */
  tone?: "ember";
}

/**
 * A page's opening on the night stage: an eyebrow or breadcrumb, the title
 * (56px, 800), a short paragraph, buttons, and the page's figures on the
 * right. The title is the page's h1.
 */
export function Hero({
  eyebrow,
  dot,
  crumbs,
  title,
  status,
  lede,
  actions,
  stats,
  statSize = "lg",
  className = "",
}: {
  eyebrow?: ReactNode;
  /** The eyebrow's dot: ember with an incident, quiet otherwise. */
  dot?: "ember" | "quiet";
  crumbs?: Crumb[];
  /** One or more lines; each array item is set on its own line. */
  title: ReactNode | ReactNode[];
  /** Beside the title, such as a StatusPill. */
  status?: ReactNode;
  lede?: ReactNode;
  actions?: ReactNode;
  stats?: HeroStat[];
  /** lg: 40px figures (Overview); md: 36px (a role). */
  statSize?: "lg" | "md";
  className?: string;
}) {
  const lines = Array.isArray(title) ? title : [title];
  return (
    <section className={`hero wrap ${className}`}>
      <div className="hero-main">
        {crumbs && (
          <nav className="hero-crumbs cap" aria-label="Breadcrumb">
            <ol>
              {crumbs.map((crumb, index) => (
                <li key={`${crumb.label}-${index}`}>
                  {crumb.href ? (
                    <a href={crumb.href}>{crumb.label}</a>
                  ) : (
                    <span aria-current="page">{crumb.label}</span>
                  )}
                </li>
              ))}
            </ol>
          </nav>
        )}
        {eyebrow && (
          <p className="hero-eyebrow cap">
            {dot && (
              <span className={`hero-dot hero-dot-${dot}`} aria-hidden="true" />
            )}
            {eyebrow}
          </p>
        )}
        <div className="hero-heading">
          <h1 className="hero-title">
            {lines.map((line, index) =>
              line === "" || line === null || line === undefined ? null : (
                <span key={index} className="hero-line">
                  {line}
                </span>
              ),
            )}
          </h1>
          {status}
        </div>
        {lede && <p className="hero-lede">{lede}</p>}
        {actions && <div className="hero-actions">{actions}</div>}
      </div>
      {stats && stats.length > 0 && (
        <dl className={`hero-stats hero-stats-${statSize}`}>
          {stats.map((stat) => (
            <div key={stat.label}>
              <dt className="cap">{stat.label}</dt>
              <dd
                className={
                  stat.tone === "ember" && stat.value > 0 ? "is-ember" : ""
                }
              >
                <DigitRoll value={stat.value} />
              </dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}
