import { useEffect, useRef, useState, type Ref } from "react";
import {
  HERO_CYCLE_MS,
  HERO_EVENTS,
  HERO_STEP_COUNT,
  HERO_STEP_MS,
  HERO_STEP_TITLES,
  heroStep,
  heroStepAt,
  type HeroStep,
} from "../content/scenario";
import {
  PLANE_LAYOUT,
  useMediaQuery,
  usePageVisible,
  useReducedMotion,
} from "../hooks/media";
import { useInView } from "../hooks/useInView";
import { usePlaneScale } from "../hooks/usePlaneScale";
import { useProgress } from "../hooks/useProgress";
import { useTimeline, type Timeline } from "../hooks/useTimeline";
import { Crossfade } from "./Crossfade";
import { DigitRoll } from "./DigitRoll";
import { DotField, type Mask } from "./DotField";
import { Flame } from "./Mark";
import { Inbox } from "./icons";
import { Rich, plain } from "./Rich";

/** Autoplay waits for the page's arrival sequence to finish. */
const ARRIVAL_MS = 1600;
const FINAL_T = HERO_CYCLE_MS - 1;

const PLANE_MASK: Mask = { cx: 0.58, cy: 0.48, rx: 0.62, ry: 0.75 };
const STACK_MASK: Mask = { cx: 0.5, cy: 0.42, rx: 0.8, ry: 0.72 };

export function Scenario() {
  const reduced = useReducedMotion();
  const plane = useMediaQuery(PLANE_LAYOUT);
  const pageVisible = usePageVisible();
  const frameRef = useRef<HTMLDivElement>(null);
  const ledgerRef = useRef<HTMLDivElement>(null);
  const { inView } = useInView(frameRef);
  const [arrived, setArrived] = useState(false);
  const [announcement, setAnnouncement] = useState("");

  usePlaneScale(frameRef, { width: 1440, content: 1330, gutter: 16 });

  useEffect(() => {
    if (reduced) return;
    const id = window.setTimeout(() => setArrived(true), ARRIVAL_MS);
    return () => window.clearTimeout(id);
  }, [reduced]);

  const timeline = useTimeline({
    events: HERO_EVENTS,
    cycle: HERO_CYCLE_MS,
    active: arrived && inView && pageVisible,
    initialT: reduced ? FINAL_T : 0,
    initialPlaying: !reduced,
  });

  // Someone turns on reduced motion mid-visit: stop on the final step.
  const { seek } = timeline;
  useEffect(() => {
    if (reduced) seek(FINAL_T, false);
  }, [reduced, seek]);

  const k = heroStepAt(timeline.t);
  const step = heroStep(k);

  const pick = (j: number) => {
    timeline.seek(j * HERO_STEP_MS, false);
    setAnnouncement(
      `Step ${j + 1} of ${HERO_STEP_COUNT}: ${plain(HERO_STEP_TITLES[j])}`,
    );
  };

  return (
    <div
      className="scene-frame"
      ref={frameRef}
      id="scenario"
      role="region"
      aria-labelledby="scenario-label"
    >
      <div className="scene">
        <DotField
          className="scene-dots"
          mask={plane ? PLANE_MASK : STACK_MASK}
          burst={k === 3 && !reduced}
          burstKey={timeline.epoch}
          originRef={ledgerRef}
        />
        <h2 className="scene-label cap" id="scenario-label">
          <span className="ember-dot" aria-hidden="true" />
          Interactive scenario
          <span className="slash" aria-hidden="true">
            /
          </span>
          <span className="visually-hidden">, </span>
          Incident 01
        </h2>
        <svg
          className="scene-leaders"
          width="1440"
          height="650"
          viewBox="0 0 1440 650"
          aria-hidden="true"
        >
          <path d="M1158 186 L1226 166" stroke="#71717a" />
          <circle cx="1155" cy="187" r="3" fill="#e4e4e7" />
          <path d="M1266 282 L1112 352" stroke="#52525b" />
        </svg>
        <p className="scene-note">
          20 separate identities.
          <br />
          No blanket shutdowns.
        </p>
        <Ledger
          ref={ledgerRef}
          step={step}
          timeline={timeline}
          onPick={pick}
          onToggle={() => timeline.setPlaying(!timeline.playing)}
        />
        <Ticket step={step} />
        <Control step={step} />
        <p className="visually-hidden" aria-live="polite">
          {announcement}
        </p>
      </div>
    </div>
  );
}

type LedgerProps = {
  ref: Ref<HTMLDivElement>;
  step: HeroStep;
  timeline: Timeline;
  onPick: (j: number) => void;
  onToggle: () => void;
};

function Ledger({ ref, step, timeline, onPick, onToggle }: LedgerProps) {
  return (
    <div className="ledger" ref={ref}>
      <div className="ledger-head">
        <span className="ledger-tile" aria-hidden="true">
          B
        </span>
        <span className="cap ledger-name">
          Exposure ledger <span className="ledger-who">/ worker-07</span>
        </span>
        <span className={`status cap tone-${step.status.tone}`}>
          <Crossfade id={step.status.text}>{step.status.text}</Crossfade>
        </span>
      </div>

      <p className="cap ledger-step">
        <span className="num">{step.number} / 06</span> &mdash; {step.tag}
      </p>
      <p className="ledger-title">
        <Crossfade id={step.title}>
          <Rich text={step.title} />
        </Crossfade>
      </p>
      <p className="ledger-desc">
        <Crossfade id={step.description}>
          <Rich text={step.description} />
        </Crossfade>
      </p>

      <dl className="counters">
        <div className="counter">
          <dt className="cap">Running</dt>
          <dd>
            <DigitRoll value={step.running} width={2} />
          </dd>
        </div>
        <div className="counter">
          <dt className="cap">Contained</dt>
          <dd className={step.contained > 0 ? "is-hot" : undefined}>
            <DigitRoll value={step.contained} width={2} />
          </dd>
        </div>
        <div className="counter counter-id">
          <dt className="cap">Identity</dt>
          <dd className="id">worker-07</dd>
        </div>
      </dl>

      <div
        className="rows"
        role="table"
        aria-label="Credentials worker-07 has seen"
      >
        <div className="row row-head cap" role="row">
          <span role="columnheader">Credential / source</span>
          <span role="columnheader">Evidence</span>
          <span role="columnheader">Action</span>
        </div>
        {step.rows.map((row) => (
          <div className="row" role="row" key={row.id}>
            <span className="row-cred" role="cell">
              <span className={`row-dot dot-${row.dot}`} aria-hidden="true" />
              <span>
                <span className={row.mono ? "row-name id" : "row-name"}>
                  {row.name}
                </span>
                <span className="row-src">
                  <Rich text={row.source} />
                </span>
              </span>
            </span>
            <span className={row.hot ? "row-ev is-hot" : "row-ev"} role="cell">
              <Crossfade id={row.evidence}>
                <Rich text={row.evidence} />
              </Crossfade>
            </span>
            <span className="row-act" role="cell">
              <span className={`act tone-${row.action.tone}`}>
                <Crossfade id={row.action.text}>{row.action.text}</Crossfade>
              </span>
            </span>
          </div>
        ))}
      </div>

      <div className="ledger-foot">
        <div className="hsteps" role="group" aria-label="Scenario steps">
          {HERO_STEP_TITLES.map((title, j) => (
            <HeroStepButton
              key={j}
              index={j}
              current={step.index}
              title={title}
              timeline={timeline}
              onPick={onPick}
            />
          ))}
        </div>
        <button type="button" className="btn btn-sm" onClick={onToggle}>
          {timeline.playing ? "Pause" : "Play"}
        </button>
      </div>
    </div>
  );
}

function HeroStepButton({
  index,
  current,
  title,
  timeline,
  onPick,
}: {
  index: number;
  current: number;
  title: string;
  timeline: Timeline;
  onPick: (j: number) => void;
}) {
  const fill = useRef<HTMLSpanElement>(null);
  const isCurrent = index === current;
  const start = index * HERO_STEP_MS;
  useProgress(fill, {
    active: isCurrent,
    start,
    end: start + HERO_STEP_MS,
    running: timeline.running,
    position: timeline.position,
    restartKey: `${current}:${timeline.epoch}`,
  });
  const state = isCurrent ? "is-current" : index < current ? "is-past" : "";
  return (
    <button
      type="button"
      className={`hstep ${state}`}
      aria-current={isCurrent ? "step" : undefined}
      aria-label={`Step ${index + 1}: ${plain(title)}`}
      onClick={() => onPick(index)}
    >
      {String(index + 1).padStart(2, "0")}
      <span className="hstep-track" aria-hidden="true">
        <span
          ref={fill}
          className="hstep-fill"
          style={
            isCurrent
              ? undefined
              : { transform: `scaleX(${index < current ? 1 : 0})` }
          }
        />
      </span>
    </button>
  );
}

function Ticket({ step }: { step: HeroStep }) {
  const { description, untrusted } = step.ticket;
  return (
    <div className="ticket stage-card">
      <div className="card-head">
        <span className="card-icon">
          <Inbox />
        </span>
        <span className="cap card-kind">Incoming ticket</span>
      </div>
      <p className="ticket-title">Support queue &middot; #4821</p>
      <p className="ticket-desc">
        <Crossfade id={description}>{description}</Crossfade>
      </p>
      <div className="ticket-foot">
        <span className={untrusted ? "pill is-hot" : "pill"}>
          Untrusted content
        </span>
        <span className="id ticket-time">17:42:08</span>
      </div>
    </div>
  );
}

function Control({ step }: { step: HeroStep }) {
  const { title, detail, count, burned } = step.control;
  return (
    <div className="control stage-card">
      <div className="card-head">
        <span className="card-icon card-icon-ember">
          <Flame size={15} />
        </span>
        <span className="cap card-kind">Control plane</span>
      </div>
      <p className="control-title">
        <Crossfade id={title}>
          <Rich text={title} />
        </Crossfade>
      </p>
      <p className="control-detail">
        <Crossfade id={detail}>
          <Rich text={detail} />
        </Crossfade>
      </p>
      <div className="cells" aria-hidden="true">
        {step.cells.map((cell, i) => (
          <span key={i} className={`cell cell-${cell}`} />
        ))}
      </div>
      <p className={burned ? "control-count is-hot" : "control-count"}>
        <Crossfade id={count}>{count}</Crossfade>
      </p>
    </div>
  );
}
