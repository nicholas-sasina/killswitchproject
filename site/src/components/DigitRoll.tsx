import { useState, type AnimationEvent } from "react";

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

/** A counter that rolls only the digits that changed, up or down. */
export function DigitRoll({ value, width }: { value: number; width: number }) {
  const [last, setLast] = useState(value);
  const [dir, setDir] = useState<Direction>("up");
  if (last !== value) {
    setDir(value > last ? "up" : "down");
    setLast(value);
  }
  const text = String(value).padStart(width, "0");
  return (
    <span className="roll num">
      <span className="visually-hidden">{value}</span>
      <span className="roll-digits" aria-hidden="true">
        {[...text].map((ch, i) => (
          <Digit key={i} ch={ch} dir={dir} />
        ))}
      </span>
    </span>
  );
}
