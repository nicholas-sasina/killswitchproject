// The "How it works" pipeline: six steps over 23.8 seconds. pipelineState(t)
// is pure: the diagram renders whatever it returns for the current time, so
// the tests, autoplay, the step buttons and reduced motion all agree.

// The times below are written for a 17 s loop. PIPELINE_PACE stretches every
// one of them by the same factor, so the rhythm holds while each step stays
// up long enough to read. Raise it to slow the diagram down.
export const PIPELINE_PACE = 1.4;
const ms = (time: number) => Math.round(time * PIPELINE_PACE);

export const PIPELINE_STARTS: readonly number[] = [
  0, 3000, 5500, 8000, 10500, 13000,
].map(ms);
export const PIPELINE_CYCLE_MS = ms(17000);
export const PIPELINE_STEP_COUNT = PIPELINE_STARTS.length;

export type PanelId =
  "ledger" | "exit" | "canary" | "decision" | "human" | "result";

/** The panel each step works in; the scan bar and the packet go there. */
export const STEP_PANEL: readonly PanelId[] = [
  "ledger",
  "exit",
  "canary",
  "decision",
  "human",
  "result",
];

export type PanelState = "idle" | "active" | "hot";
export type LineTone = "idle" | "ink" | "ember";

// When each detail inside a step lights up, in ms from the start of the loop.
const AT = {
  mark: [500, 1000, 1500, 2000].map(ms),
  ledgerDone: ms(2500),
  chip: [3500, 4000, 4500].map(ms),
  exitDone: ms(5000),
  identified: ms(6000),
  credential: ms(8500),
  decisionDone: ms(9000),
  shared: ms(11000),
  audit: ms(11500),
  result: [13000, 13500, 14000, 14500].map(ms),
};

/** Every moment the diagram changes. The page re-renders only at these. */
export const PIPELINE_EVENTS: readonly number[] = [
  ...new Set([
    ...PIPELINE_STARTS,
    ...AT.mark,
    AT.ledgerDone,
    ...AT.chip,
    AT.exitDone,
    AT.identified,
    AT.credential,
    AT.decisionDone,
    AT.shared,
    AT.audit,
    ...AT.result,
  ]),
].sort((a, b) => a - b);

export type PipelineState = {
  t: number;
  step: number;
  panels: Record<PanelId, PanelState>;
  support: { hot: boolean; status: string };
  ledger: { marks: boolean[]; done: boolean };
  exit: { chips: boolean[]; done: boolean };
  canary: { hot: boolean; identified: boolean };
  decision: { burn: boolean; credential: boolean; done: boolean };
  human: { escalated: boolean; shared: boolean; audit: boolean };
  result: boolean[];
  /** The connector into each panel, by the panel it leads to. */
  lines: Record<Exclude<PanelId, "ledger">, LineTone>;
};

export const PIPELINE_STEPS: readonly { title: string; body: string }[] = [
  {
    title: "Twenty workers, each with its own identity.",
    body: "Every tool call passes through the Gateway, and the exposure ledger records which instance saw which credential, and from what source.",
  },
  {
    title: "Exit checks screen what leaves.",
    body: "Outgoing messages and network traffic are checked for every key Burn issued. A recognized leak is blocked before it gets out.",
  },
  {
    title: "A decoy key is used from outside.",
    body: "Each session gets its own decoy key. Using it means showing it to a service Burn watches, so a use from outside points to one session: `worker-07`, the support agent.",
  },
  {
    title: "Burn contains that worker only.",
    body: "Its identity is revoked and the keys it owned are rotated. Shared keys whose value it saw are flagged for review, never rotated blindly.",
  },
  {
    title: "People review only what needs them.",
    body: "Shared keys whose value an instance saw, and anything Burn couldn’t fully contain, go on one review list with the evidence attached.",
  },
  {
    title: "One worker contained. Nineteen keep running.",
    body: "Burn doesn’t assume every key is compromised. It acts on evidence, one instance and one key at a time.",
  },
];

const wrap = (t: number) =>
  ((t % PIPELINE_CYCLE_MS) + PIPELINE_CYCLE_MS) % PIPELINE_CYCLE_MS;

/** The step showing at time t. */
export function pipelineStepAt(t: number): number {
  const time = wrap(t);
  let s = 0;
  PIPELINE_STARTS.forEach((start, i) => {
    if (time >= start) s = i;
  });
  return s;
}

/** When step s starts and ends, in ms from the start of the loop. */
export function pipelineStepSpan(s: number): { start: number; end: number } {
  const start = PIPELINE_STARTS[s];
  const end =
    s + 1 < PIPELINE_STARTS.length ? PIPELINE_STARTS[s + 1] : PIPELINE_CYCLE_MS;
  return { start, end };
}

/** The moment step s is fully played out: what a step button shows. */
export function pipelineStepDone(s: number): number {
  return pipelineStepSpan(s).end - 1;
}

export function pipelineState(time: number): PipelineState {
  const t = wrap(time);
  const s = pipelineStepAt(t);
  const at = (ms: number) => t >= ms;
  const line = (from: number, tone: LineTone = "ink"): LineTone =>
    s >= from ? tone : "idle";

  return {
    t,
    step: s,
    panels: {
      ledger: s === 0 ? "active" : "idle",
      exit: s === 1 ? "active" : "idle",
      canary: s >= 2 ? "hot" : "idle",
      decision: s >= 3 ? "active" : "idle",
      human: s === 4 ? "active" : "idle",
      result: s === 5 ? "active" : "idle",
    },
    support: {
      hot: s >= 2,
      status:
        s >= 3 ? "burned" : s >= 2 ? "identified" : "independent identity",
    },
    ledger: { marks: AT.mark.map(at), done: at(AT.ledgerDone) },
    exit: { chips: AT.chip.map(at), done: at(AT.exitDone) },
    canary: { hot: s >= 2, identified: at(AT.identified) },
    decision: {
      burn: s >= 3,
      credential: at(AT.credential),
      done: at(AT.decisionDone),
    },
    human: { escalated: s >= 4, shared: at(AT.shared), audit: at(AT.audit) },
    result: AT.result.map(at),
    lines: {
      exit: line(1),
      canary: line(2),
      decision: line(3, "ember"),
      human: line(4),
      result: line(5),
    },
  };
}
