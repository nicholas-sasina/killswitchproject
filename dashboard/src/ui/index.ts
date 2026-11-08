// The console's shared pieces. Pages import from here and never fork them.
export { Card, CardNote } from "./Card";
export { Crossfade } from "./Crossfade";
export { Dialog } from "./Dialog";
export { DigitRoll } from "./DigitRoll";
export { Hero, type Crumb, type HeroStat } from "./Hero";
export { Flame, Mark } from "./Mark";
export { Pill, StatusPill } from "./Pill";
export { Segmented, type SegmentOption } from "./Segmented";
export { Square } from "./Square";
export {
  Chip,
  EmptyState,
  Facts,
  Figures,
  Notice,
  type Fact,
  type Figure,
} from "./bits";
export {
  prefersReducedMotion,
  useMediaQuery,
  useReducedMotion,
} from "./motion";
export { PulseContext } from "./pulse";
