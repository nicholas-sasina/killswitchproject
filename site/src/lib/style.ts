import type { CSSProperties } from "react";

/** Inline CSS custom properties, typed. */
export function cssVars(vars: Record<`--${string}`, string | number>) {
  return vars as CSSProperties;
}
