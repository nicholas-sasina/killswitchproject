import { cssVars } from "../lib/style";
import { Scenario } from "./Scenario";

export function Hero() {
  return (
    <section className="hero" id="top" aria-labelledby="hero-title">
      <div className="hero-inner">
        <div className="hero-copy">
          <p className="hero-eyebrow cap">
            <span className="ember-dot" aria-hidden="true" />
            Built for the age of autonomous agents
            <span className="slash" aria-hidden="true">
              /
            </span>
            <span className="visually-hidden">, </span>
            AWS &times; A10
          </p>
          <h1 className="hero-title" id="hero-title">
            <span className="hero-line">
              <span className="hero-line-inner" style={cssVars({ "--i": 0 })}>
                One agent goes rogue.
              </span>
            </span>{" "}
            <span className="hero-line">
              <span className="hero-line-inner" style={cssVars({ "--i": 1 })}>
                The other nineteen don&rsquo;t.
              </span>
            </span>
          </h1>
          <p className="hero-sub">
            AI agents don&rsquo;t stop being dangerous when you switch them off.{" "}
            <strong>
              Burn traces credential exposure, isolates the compromised
              instance, and responds to each key based on evidence.
            </strong>
          </p>
          <p className="hero-caption cap">
            <span className="hero-caption-rule" aria-hidden="true" />
            One worker contained. The swarm keeps running.
          </p>
        </div>
        <Scenario />
      </div>
    </section>
  );
}
