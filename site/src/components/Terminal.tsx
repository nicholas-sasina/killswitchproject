import { useEffect, useLayoutEffect, useRef } from "react";
import { useRevealOnce, type RevealPhase } from "../hooks/useRevealOnce";
import { cssVars } from "../lib/style";

export type TermLine = {
  text: string;
  /** Prompt and comment lines are set quieter than output. */
  muted?: boolean;
  /** A shell prompt to show before the text. */
  prompt?: boolean;
  /** Lines with the same `at` print together (a command split over lines). */
  at?: number;
  /** Part of the line that turns from one value into another once printed. */
  swap?: { before: string; from: string; to: string; after: string };
};

const LINE_MS = 140;
/** A beat after the line prints, before its value is swapped. */
const SWAP_DELAY_MS = 450;
const SWAP_MS = 600;
const NOISE = "abcdefghijklmnopqrstuvwxyz0123456789{}:/_";

export function Terminal({ lines }: { lines: TermLine[] }) {
  const ref = useRef<HTMLPreElement>(null);
  const phase = useRevealOnce(ref);
  return (
    <pre className={`term is-${phase}`} ref={ref}>
      {lines.map((line, i) => {
        const at = line.at ?? i;
        return (
          <span
            key={i}
            className={line.muted ? "t-line t-muted" : "t-line"}
            style={cssVars({ "--at": at })}
          >
            {line.prompt && <span className="t-prompt">$ </span>}
            {line.swap ? (
              // A long value wraps only between its parts, never at a
              // hyphen inside a host name.
              <>
                <span className="t-part">{line.swap.before}</span>
                <wbr />
                <Swap
                  from={line.swap.from}
                  to={line.swap.to}
                  phase={phase}
                  delay={at * LINE_MS + SWAP_DELAY_MS}
                />
                <wbr />
                <span className="t-part">{line.swap.after}</span>
              </>
            ) : (
              line.text || " "
            )}
          </span>
        );
      })}
    </pre>
  );
}

/**
 * Scrambles `from` into `to`, left to right, character by character. It
 * writes the text node directly for its 600 ms, so React never re-renders.
 */
function Swap({
  from,
  to,
  phase,
  delay,
}: {
  from: string;
  to: string;
  phase: RevealPhase;
  delay: number;
}) {
  const ref = useRef<HTMLSpanElement>(null);

  // The span's text is owned here, not by React, so the frames below can
  // rewrite it without a render.
  useLayoutEffect(() => {
    if (ref.current) ref.current.textContent = phase === "static" ? to : from;
  }, [phase, from, to]);

  useEffect(() => {
    const el = ref.current;
    if (!el || phase !== "play") return;
    let raf = 0;
    const length = Math.max(from.length, to.length);
    const timer = window.setTimeout(() => {
      const start = performance.now();
      const frame = (now: number) => {
        const p = Math.min(1, (now - start) / SWAP_MS);
        let out = "";
        for (let i = 0; i < length; i++) {
          // Each position settles at its own moment; just before, it flickers.
          const settle = (i + 1) / length;
          if (p >= settle) out += to[i] ?? "";
          else if (p >= settle - 0.18)
            out += NOISE[Math.floor(Math.random() * NOISE.length)];
          else out += from[i] ?? "";
        }
        el.textContent = out;
        if (p < 1) raf = requestAnimationFrame(frame);
        else el.textContent = to;
      };
      raf = requestAnimationFrame(frame);
    }, delay);
    return () => {
      window.clearTimeout(timer);
      cancelAnimationFrame(raf);
    };
  }, [phase, from, to, delay]);

  return <span ref={ref} className="t-swap" />;
}
