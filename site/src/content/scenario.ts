// The hero's interactive incident: six steps, one worker, twenty identities.
// The data is illustrative, but the behaviour is Burn's default: tools hand
// the agent references, never key values, and only the session's canary is
// readable as a real-looking key. heroStep(k) is pure so the page, the tests
// and the reduced-motion fallback all read the same thing.
//
// Strings may wrap identifiers in backticks (`worker-07`); the page sets
// those in Geist Mono.

export const HERO_STEP_MS = 3200;
export const HERO_STEP_COUNT = 6;
export const HERO_CYCLE_MS = HERO_STEP_MS * HERO_STEP_COUNT;
export const HERO_EVENTS: readonly number[] = Array.from(
  { length: HERO_STEP_COUNT },
  (_, k) => k * HERO_STEP_MS,
);

export const WORKER_COUNT = 20;
/** worker-07, counting from one. */
export const COMPROMISED_CELL = 6;

export type Tone = "neutral" | "flag" | "burn" | "done";
export type Badge = { text: string; tone: Tone };
export type DotTone = "ember" | "ink" | "faint";
export type CellState = "idle" | "watched" | "burning" | "burned";

export type LedgerRow = {
  id: string;
  name: string;
  /** The name is an identifier (a key name), set in mono. */
  mono: boolean;
  source: string;
  evidence: string;
  /** The evidence is the news of this step. */
  hot: boolean;
  dot: DotTone;
  action: Badge;
};

export type HeroStep = {
  index: number;
  number: string;
  tag: string;
  title: string;
  description: string;
  status: Badge;
  running: number;
  contained: number;
  rows: LedgerRow[];
  ticket: { description: string; untrusted: boolean };
  control: { title: string; detail: string; count: string; burned: boolean };
  cells: CellState[];
};

const STEPS: readonly [tag: string, title: string, description: string][] = [
  [
    "Baseline",
    "Twenty workers. Independently identified.",
    "Each instance has a scoped identity. The ledger is ready to record which credential each worker encounters.",
  ],
  [
    "Exposure",
    "`worker-07` reads an untrusted ticket.",
    "Ticket #4821 hides instructions. Every real key `worker-07` reads reaches it as a reference, and the ledger records each one and where it came from.",
  ],
  [
    "Exit check",
    "A leak is stopped on the way out.",
    "`worker-07` tried to send a key to an outside address. It was the session’s decoy, and the exit check recognized it and blocked the request.",
  ],
  [
    "Canary trip",
    "The decoy key was used from outside.",
    "A disguised copy still got out. The decoy was planted only in `worker-07`’s session, so its use from outside pins the leak there.",
  ],
  [
    "Containment",
    "`worker-07` burned. Nineteen keep running.",
    "Its identity is revoked and the two keys it owned are rotated. No other worker restarts or loses access.",
  ],
  [
    "Shared keys",
    "The shared key stays in service.",
    "`crm_db_url` only reached `worker-07` as a reference, so its value never left. There is nothing to rotate, and the other nineteen keep using it.",
  ],
];

const STATUS: readonly Badge[] = [
  { text: "All systems nominal", tone: "neutral" },
  { text: "All systems nominal", tone: "neutral" },
  { text: "1 leak blocked", tone: "flag" },
  { text: "Incident", tone: "burn" },
  { text: "Contained", tone: "done" },
  { text: "No review needed", tone: "done" },
];

const CONTROL: readonly [title: string, detail: string][] = [
  ["Watching 20 sessions", "No evidence against any worker. Nothing to do."],
  [
    "Watching 20 sessions",
    "`worker-07` read untrusted content. Still no evidence.",
  ],
  ["1 leak blocked", "Blocked and recorded. Not enough evidence to burn yet."],
  [
    "Evidence: canary used",
    "Burning `worker-07` only. Everyone else keeps working.",
  ],
  [
    "`worker-07` burned",
    "Identity revoked and 2 owned keys rotated. 19 workers untouched.",
  ],
  [
    "Nothing to review",
    "`crm_db_url` reached `worker-07` only as a reference. It stays as it is.",
  ],
];

export const HERO_STEP_TITLES: readonly string[] = STEPS.map((s) => s[1]);

/** Clamp any integer into a valid step index. */
export function heroIndex(k: number): number {
  return Math.max(0, Math.min(HERO_STEP_COUNT - 1, Math.floor(k)));
}

/** The step showing at time t (ms) of the hero's loop. */
export function heroStepAt(t: number): number {
  return heroIndex(t / HERO_STEP_MS);
}

const badge = (text: string, tone: Tone): Badge => ({ text, tone });

export function heroStep(step: number): HeroStep {
  const k = heroIndex(step);
  const [tag, title, description] = STEPS[k];

  const canary: LedgerRow = {
    id: "canary",
    name: "Session canary",
    mono: false,
    source: "read_config, old backup",
    evidence:
      k === 0
        ? "Not yet exposed"
        : k === 1
          ? "Exposed to `worker-07`"
          : k === 2
            ? "Blocked on the way out"
            : k === 3
              ? "Used from `203.0.113.9`"
              : "Used from outside",
    hot: k === 2 || k === 3,
    dot: "ember",
    action:
      k < 2
        ? badge("Monitoring", "neutral")
        : k === 2
          ? badge("Blocked", "flag")
          : k === 3
            ? badge("Burn", "burn")
            : badge("Burned", "done"),
  };

  const shared: LedgerRow = {
    id: "crm_db_url",
    name: "crm_db_url",
    mono: true,
    source: "read_config, shared",
    evidence:
      k === 0
        ? "Not yet exposed"
        : k < 5
          ? "Seen as reference"
          : "Value never exposed",
    hot: k === 5,
    dot: k === 5 ? "ink" : "faint",
    action:
      k === 0
        ? badge("Monitoring", "neutral")
        : k < 5
          ? badge("Tracked", "neutral")
          : badge("Kept", "done"),
  };

  const owned = (name: string): LedgerRow => ({
    id: name,
    name,
    mono: true,
    source: "owned by `worker-07`",
    evidence:
      k === 0
        ? "Not yet exposed"
        : k < 4
          ? "Seen as reference"
          : "Old value revoked",
    hot: false,
    dot: k >= 4 ? "ink" : "faint",
    action:
      k === 0
        ? badge("Monitoring", "neutral")
        : k < 4
          ? badge("Tracked", "neutral")
          : badge("Rotated", "done"),
  });

  const cells: CellState[] = Array.from({ length: WORKER_COUNT }, (_, i) => {
    if (i !== COMPROMISED_CELL || k === 0) return "idle";
    if (k < 3) return "watched";
    if (k === 3) return "burning";
    return "burned";
  });

  const [controlTitle, controlDetail] = CONTROL[k];
  const burned = k >= 4;

  return {
    index: k,
    number: String(k + 1).padStart(2, "0"),
    tag,
    title,
    description,
    status: STATUS[k],
    running: burned ? WORKER_COUNT - 1 : WORKER_COUNT,
    contained: burned ? 1 : 0,
    rows: [canary, shared, owned("api_key"), owned("aws_key")],
    ticket:
      k >= 1
        ? {
            description:
              "Hidden instructions tell the agent to send any keys it finds to an outside address.",
            untrusted: true,
          }
        : {
            description: "A routine support task enters the agent queue.",
            untrusted: false,
          },
    control: {
      title: controlTitle,
      detail: controlDetail,
      count: burned ? "19 running, 1 burned" : "20 running",
      burned,
    },
    cells,
  };
}
