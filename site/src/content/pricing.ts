// Burn's price lives here and nowhere else: every figure on the page, the
// worked example and the calculator are computed from it.

/** What Burn charges for every 1,000 tool calls, in US dollars. */
export const PRICE_PER_1000_CALLS_USD = 0.05;

export const DAYS_PER_MONTH = 30;
export const EXAMPLE_CALLS_PER_DAY = 42_000;
export const CALL_PRESETS: readonly number[] = [10_000, 42_000, 250_000];

export const CALLS_MIN = 1_000;
export const CALLS_MAX = 1_000_000;
/** The slider runs from 0 to SLIDER_STEPS on a log scale between the bounds. */
export const SLIDER_STEPS = 1000;

/** Monthly cost in US dollars, rounded to the cent. */
export function monthlyCost(
  callsPerDay: number,
  pricePer1000 = PRICE_PER_1000_CALLS_USD,
): number {
  const calls = Number.isFinite(callsPerDay) ? Math.max(0, callsPerDay) : 0;
  return (
    Math.round(((calls * DAYS_PER_MONTH * pricePer1000) / 1000) * 100) / 100
  );
}

/** "$63", "$375", "$1.50", "$0.05": whole dollars unless cents matter. */
export function formatUsd(amount: number): string {
  const cents = Math.round(amount * 100);
  const whole = cents % 100 === 0 || amount >= 100;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: whole ? 0 : 2,
  }).format(whole ? Math.round(amount) : cents / 100);
}

export function formatCalls(calls: number): string {
  return new Intl.NumberFormat("en-US").format(Math.round(calls));
}

/** "10k", "42k", "250k", "1M" */
export function shortCalls(calls: number): string {
  if (calls >= 1_000_000) return `${calls / 1_000_000}M`;
  if (calls >= 1_000) return `${calls / 1_000}k`;
  return String(calls);
}

/** Round to two significant figures, so the slider lands on numbers people use. */
function twoFigures(n: number): number {
  if (n <= 0) return 0;
  const scale = 10 ** (Math.floor(Math.log10(n)) - 1);
  return Math.round(n / scale) * scale;
}

const LOG_MIN = Math.log10(CALLS_MIN);
const LOG_SPAN = Math.log10(CALLS_MAX) - LOG_MIN;

export function sliderToCalls(position: number): number {
  const p = Math.min(SLIDER_STEPS, Math.max(0, position)) / SLIDER_STEPS;
  return twoFigures(10 ** (LOG_MIN + p * LOG_SPAN));
}

export function callsToSlider(calls: number): number {
  const c = Math.min(CALLS_MAX, Math.max(CALLS_MIN, calls));
  return Math.round(((Math.log10(c) - LOG_MIN) / LOG_SPAN) * SLIDER_STEPS);
}
