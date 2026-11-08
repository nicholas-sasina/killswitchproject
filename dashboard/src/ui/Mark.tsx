// Burn's mark, from the website (site/src/components/Mark.tsx): the flame and
// the wordmark. The console's navigation shows it at the board's size.
import "./ui.css";

const FLAME =
  "M10.2 1C11.3 4.6 16 6.9 16 12.6A7 7 0 0 1 2 12.9c0-3 1.6-5.1 3.4-6.5.1 2.3 1.1 3.8 2.7 4.4C7.4 7.2 8.4 3.8 10.2 1Z";

export function Flame({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={(size * 18) / 22}
      height={size}
      viewBox="0 0 18 22"
      aria-hidden="true"
    >
      <path d={FLAME} fill="var(--ember)" />
    </svg>
  );
}

export function Mark({ size = "md" }: { size?: "sm" | "md" | "nav" }) {
  return (
    <span className={`mark mark-${size}`}>
      <Flame size={size === "sm" ? 16 : size === "nav" ? 20 : 22} />
      <span className="mark-word">
        BURN<span className="mark-dot">.</span>
      </span>
    </span>
  );
}
