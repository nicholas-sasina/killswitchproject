import { useState, type AnimationEvent } from "react";
import "./ui.css";

// Ported from the website (site/src/components/DigitRoll.tsx). Digits are
// keyed from the right, so the units stay the units when 9 becomes 10, and
// `width` pads with zeros only when asked to.

type Direction = "up" | "down";

/** One digit slot: the new digit rolls in as the old one rolls out. */
function Digit({ ch, dir }: { ch: string; dir: Direction }) {
  const [shown, setShown] = useState(ch);
  const [out, setOut] = useState<string | null>(null);
  const [moved, setMoved] = useState(false);

  if (shown !== ch) {
    setOut(shown);
    setShown(ch);
    setMoved(true);
  }

  const done = (e: AnimationEvent) => {
    if (e.target === e.currentTarget) setOut(null);
  };

  return (
    <span className={`digit digit-${dir}`}>
      <span key={shown} className={moved ? "digit-in" : undefined}>
        {shown}
      </span>
      {out !== null && (
        <span className="digit-out" onAnimationEnd={done}>
          {out}
        </span>
      )}
    </span>
  );
}

/**
 * A counter that rolls only the digits that changed, up or down, one digit
 * at a time. With reduced motion it swaps instantly (styles/motion.css).
 * Thousands are grouped ("41,928"); the separators do not roll.
 */
export function DigitRoll({
  value,
  width = 1,
  group = true,
  className = "",
}: {
  value: number;
  width?: number;
  group?: boolean;
  className?: string;
}) {
  const [last, setLast] = useState(value);
  const [dir, setDir] = useState<Direction>("up");
  if (last !== value) {
    setDir(value > last ? "up" : "down");
    setLast(value);
  }
  const digits = String(Math.max(0, Math.round(value))).padStart(width, "0");
  const text = group ? digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",") : digits;
  const chars = [...text];
  return (
    <span className={`roll num ${className}`}>
      <span className="visually-hidden">{text}</span>
      <span className="roll-digits" aria-hidden="true">
        {chars.map((ch, i) =>
          ch === "," ? (
            <span key={`sep-${chars.length - i}`}>,</span>
          ) : (
            <Digit key={chars.length - i} ch={ch} dir={dir} />
          ),
        )}
      </span>
    </span>
  );
}
