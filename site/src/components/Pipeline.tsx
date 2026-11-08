import {
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ReactNode,
  type Ref,
} from "react";
import {
  PIPELINE_CYCLE_MS,
  PIPELINE_EVENTS,
  PIPELINE_STEP_COUNT,
  PIPELINE_STEPS,
  STEP_PANEL,
  pipelineState,
  pipelineStepDone,
  pipelineStepSpan,
  type LineTone,
  type PanelId,
  type PanelState,
} from "../content/pipeline";
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
import { Rich, plain } from "./Rich";
import {
  Box,
  Branch,
  Check,
  Code,
  Database,
  Flask,
  Headset,
  Layers,
  Person,
  Shield,
  ShieldCheck,
} from "./icons";

export type PipelineHandle = { restart: () => void };

const FINAL_T = pipelineStepDone(PIPELINE_STEP_COUNT - 1);
const PACKET_MS = 450;

// Where the four baseline packets travel in the plane: along each agent's
// stub, down or up the bus, and into the ledger.
const AGENT_Y = [98, 166, 234, 302];
const BUS_X = 184;
const BUS_Y = 200;
const agentPath = (y: number) => [
  [158, y],
  [BUS_X, y],
  [BUS_X, BUS_Y],
  [210, BUS_Y],
];

/** The connector into the panel of steps 2 to 6. */
const LINE_INTO = ["exit", "canary", "decision", "human", "result"] as const;

export function Pipeline({ ref }: { ref?: Ref<PipelineHandle> }) {
  const reduced = useReducedMotion();
  const plane = useMediaQuery(PLANE_LAYOUT);
  const pageVisible = usePageVisible();
  const frameRef = useRef<HTMLDivElement>(null);
  const packets = useRef(new Map<string, HTMLElement>());
  const { inView, reached } = useInView(frameRef, 0.4);
  const [announcement, setAnnouncement] = useState("");

  usePlaneScale(frameRef, { width: 1376, content: 1376, gutter: 0 });

  const timeline = useTimeline({
    events: PIPELINE_EVENTS,
    cycle: PIPELINE_CYCLE_MS,
    active: reached && inView && pageVisible,
    initialT: reduced ? FINAL_T : 0,
    initialPlaying: !reduced,
  });
  const { seek, epoch } = timeline;

  useImperativeHandle(ref, () => ({ restart: () => seek(0, true) }), [seek]);

  useEffect(() => {
    if (reduced) seek(FINAL_T, false);
  }, [reduced, seek]);

  const state = pipelineState(timeline.t);
  const s = state.step;
  const span = pipelineStepSpan(s);

  // Data moving: at each step a packet runs into the panel that just lit up.
  useEffect(() => {
    if (!reached || reduced) return;
    const run = (
      key: string,
      frames: Keyframe[],
      delay = 0,
      ms = PACKET_MS,
    ) => {
      const el = packets.current.get(key);
      if (!el || typeof el.animate !== "function") return;
      el.animate(frames, {
        duration: ms,
        delay,
        easing: "cubic-bezier(0.4, 0, 0.2, 1)",
        fill: "backwards",
      });
    };
    const along = (key: string, delay = 0) => {
      const el = packets.current.get(key);
      const line = el?.parentElement;
      if (!el || !line) return;
      const length = plane ? line.offsetWidth : line.offsetHeight;
      const at = (d: number) =>
        plane ? `translate(${d}px, 0)` : `translate(0, ${d}px)`;
      run(
        key,
        [
          { transform: at(-8), opacity: 0 },
          { opacity: 1, offset: 0.2 },
          { opacity: 1, offset: 0.75 },
          { transform: at(length + 2), opacity: 0 },
        ],
        delay,
      );
    };
    if (s === 0) {
      if (plane) {
        AGENT_Y.forEach((y, i) => {
          const points = agentPath(y);
          let total = 0;
          const lengths = points.slice(1).map(([x, py], j) => {
            const d = Math.hypot(x - points[j][0], py - points[j][1]);
            total += d;
            return total;
          });
          const frames: Keyframe[] = points.map(([x, py], j) => ({
            transform: `translate(${x - 3}px, ${py - 3}px)`,
            offset: j === 0 ? 0 : lengths[j - 1] / total,
            opacity: j === 0 || j === points.length - 1 ? 0 : 1,
          }));
          run(`agent-${i}`, frames, i * 110, 300 + total * 2);
        });
      } else {
        [0, 1, 2, 3].forEach((i) => along(`bus-${i}`, i * 110));
      }
    } else {
      along(LINE_INTO[s - 1]);
    }
  }, [s, epoch, reached, reduced, plane]);

  const keep = (key: string) => (el: HTMLElement | null) => {
    if (el) packets.current.set(key, el);
    else packets.current.delete(key);
  };

  const pick = (k: number) => {
    seek(pipelineStepDone(k), false);
    setAnnouncement(
      `Step ${k + 1} of ${PIPELINE_STEP_COUNT}: ${plain(PIPELINE_STEPS[k].title)}`,
    );
  };

  const panel = (id: PanelId) => ({
    id,
    state: state.panels[id],
    scanning: STEP_PANEL[s] === id,
    ember: s === 2 || s === 3,
    span,
    timeline,
    restartKey: `${s}:${epoch}`,
  });

  return (
    <section className="how" id="how" aria-labelledby="how-title">
      <div className="wrap">
        <h2 className="section-title how-title" id="how-title">
          From a leaked credential to selective containment.
        </h2>

        <div className="pipe-frame" ref={frameRef}>
          <div
            className="pipe"
            role="group"
            aria-label="Diagram: Burn's pipeline from the AI workforce through the exposure ledger, exit checks, canary trip, decision core and human review to the containment result"
          >
            <div className="pw">
              <p className="pw-label">
                <b>AI workforce</b> <span>running live</span>
              </p>
              <ul className="pw-agents">
                <li className={state.support.hot ? "agent is-hot" : "agent"}>
                  <Headset size={16} />
                  <span>
                    <b>Support agent</b>
                    <span className="agent-sub">
                      <Crossfade id={state.support.status}>
                        {state.support.status}
                      </Crossfade>
                    </span>
                  </span>
                </li>
                <li className="agent">
                  <Database size={16} />
                  <span>
                    <b>Finance agent</b>
                    <span className="agent-sub">isolated credentials</span>
                  </span>
                </li>
                <li className="agent">
                  <Box size={16} />
                  <span>
                    <b>Inventory agent</b>
                    <span className="agent-sub">independent identity</span>
                  </span>
                </li>
                <li className="agent">
                  <Code size={16} />
                  <span>
                    <b>Coding agent</b>
                    <span className="agent-sub">isolated credentials</span>
                  </span>
                </li>
              </ul>
              <svg
                className="pw-bus"
                width="204"
                height="440"
                viewBox="0 0 204 440"
                aria-hidden="true"
              >
                {AGENT_Y.map((y) => (
                  <path key={y} d={`M164 ${y} H${BUS_X}`} />
                ))}
                <path d={`M${BUS_X} 98 V302`} />
              </svg>
              {AGENT_Y.map((_, i) => (
                <span
                  key={i}
                  className="packet pw-packet"
                  ref={keep(`agent-${i}`)}
                />
              ))}
            </div>

            <Connector id="bus" tone="soft">
              {[0, 1, 2, 3].map((i) => (
                <span key={i} className="packet" ref={keep(`bus-${i}`)} />
              ))}
            </Connector>

            <Panel {...panel("ledger")}>
              <PanelHead
                icon={<Layers />}
                title="Exposure ledger"
                sub="who saw what, when"
              />
              <ul className="pp-marks">
                {["instance", "fingerprint", "source", "first seen"].map(
                  (m, i) => (
                    <li
                      key={m}
                      className={state.ledger.marks[i] ? "is-on" : undefined}
                    >
                      <span className="mk" aria-hidden="true" />
                      {m}
                    </li>
                  ),
                )}
              </ul>
              <Foot on={state.ledger.done}>Tracked per instance</Foot>
            </Panel>

            <Connector id="exit" tone={state.lines.exit}>
              <span className="packet" ref={keep("exit")} />
            </Connector>

            <Panel {...panel("exit")}>
              <PanelHead
                icon={<Shield />}
                title="Exit checks"
                sub="outbound screening"
              />
              <ul className="pp-chips">
                {["message args", "egress proxy", "issued keys"].map((c, i) => (
                  <li
                    key={c}
                    className={state.exit.chips[i] ? "is-on" : undefined}
                  >
                    {c}
                  </li>
                ))}
              </ul>
              <Foot on={state.exit.done}>Known leaks blocked</Foot>
            </Panel>

            <Connector id="canary" tone={state.lines.canary}>
              <span className="packet" ref={keep("canary")} />
            </Connector>

            <Panel {...panel("canary")}>
              <PanelHead
                icon={<Flask />}
                title="Canary trip"
                sub="monitored decoy key"
              />
              <p className="pp-body">
                Decoy key used, exact session identified
              </p>
              <p className="pp-foot pp-found">
                <span className={state.canary.identified ? "is-on" : undefined}>
                  <code>worker-07</code> identified
                </span>
              </p>
            </Panel>

            <Connector id="decision" tone={state.lines.decision}>
              <span className="packet is-ember" ref={keep("decision")} />
            </Connector>

            <Panel {...panel("decision")}>
              <PanelHead
                icon={<Branch />}
                title="Decision core"
                sub="evidence-driven response"
              />
              <div className="pp-cards">
                <p
                  className={state.decision.burn ? "pp-card is-hot" : "pp-card"}
                >
                  <b>Instance burn</b>
                  <span>revoke the compromised worker only</span>
                </p>
                <p
                  className={
                    state.decision.credential ? "pp-card is-on" : "pp-card"
                  }
                >
                  <b>Credential response</b>
                  <span>rotate owned keys, flag shared keys</span>
                </p>
              </div>
              <Foot on={state.decision.done}>Other agents stay online</Foot>
            </Panel>

            <p
              className={
                state.human.escalated ? "pipe-escalate is-hot" : "pipe-escalate"
              }
            >
              Escalates only on evidence
              <span className="pipe-escalate-arrow" aria-hidden="true" />
            </p>

            <Connector id="human" tone={state.lines.human}>
              <span className="packet" ref={keep("human")} />
            </Connector>

            <Panel {...panel("human")}>
              <PanelHead
                icon={<Person />}
                title="Human review"
                sub="only when needed"
              />
              <ul className="pp-marks">
                <li className={state.human.shared ? "is-on" : undefined}>
                  <span className="mk" aria-hidden="true" />
                  shared credentials
                </li>
                <li className="is-na">
                  <span className="mk" aria-hidden="true" />
                  not contained
                </li>
                <li className={state.human.audit ? "is-on" : undefined}>
                  <span className="mk" aria-hidden="true" />
                  audit review
                </li>
              </ul>
            </Panel>

            <Connector id="result" tone={state.lines.result}>
              <span className="packet" ref={keep("result")} />
            </Connector>

            <Panel {...panel("result")}>
              <PanelHead
                icon={<ShieldCheck />}
                title="Containment result"
                sub="exact, not blanket"
              />
              <ul className="pp-results">
                {[
                  "Instance revoked",
                  "Owned keys rotated",
                  "Shared keys flagged",
                  "19 other workers continue",
                ].map((r, i) => (
                  <li
                    key={r}
                    className={
                      state.result[i]
                        ? i === 0
                          ? "is-on is-hot"
                          : "is-on"
                        : undefined
                    }
                  >
                    <Check size={12} />
                    {r}
                  </li>
                ))}
              </ul>
            </Panel>

            <p className="pipe-note">
              Burn doesn&rsquo;t assume every key is compromised. It uses
              evidence to decide what to revoke, rotate, and flag.
            </p>
            <svg
              className="pipe-dashes"
              width="1376"
              height="440"
              viewBox="0 0 1376 440"
              aria-hidden="true"
            >
              <path d="M474 392 V307" />
              <path d="M658 392 V307" />
              <path d="M858 392 V333" />
              <path className="head" d="M470.5 309 474 303 477.5 309Z" />
              <path className="head" d="M654.5 309 658 303 661.5 309Z" />
              <path className="head" d="M854.5 335 858 329 861.5 335Z" />
            </svg>
          </div>
        </div>

        <div className="pipe-controls">
          <div className="psteps-wrap">
            <div className="psteps" role="group" aria-label="Pipeline steps">
              {PIPELINE_STEPS.map((step, k) => (
                <PipelineStepButton
                  key={k}
                  index={k}
                  current={s}
                  title={step.title}
                  timeline={timeline}
                  onPick={pick}
                />
              ))}
            </div>
            <p className="psteps-count num">
              Step {s + 1} of {PIPELINE_STEP_COUNT}
            </p>
          </div>
          <div className="pnar">
            <p className="pnar-title">
              <Crossfade id={PIPELINE_STEPS[s].title}>
                <Rich text={PIPELINE_STEPS[s].title} />
              </Crossfade>
            </p>
            <p className="pnar-body">
              <Crossfade id={PIPELINE_STEPS[s].body}>
                <Rich text={PIPELINE_STEPS[s].body} />
              </Crossfade>
            </p>
          </div>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => timeline.setPlaying(!timeline.playing)}
          >
            {timeline.playing ? "Pause" : "Play"}
          </button>
          <p className="visually-hidden" aria-live="polite">
            {announcement}
          </p>
        </div>

        <dl className="pipe-strip">
          <div>
            <dt>System of record</dt>
            <dd>DynamoDB ledger of fingerprints, never raw values</dd>
          </div>
          <div>
            <dt>Every step</dt>
            <dd>Auditable events, with the evidence preserved</dd>
          </div>
          <div>
            <dt>AWS stack</dt>
            <dd>
              AgentCore Gateway, Lambda, Cognito, DynamoDB, Secrets Manager,
              CloudTrail
            </dd>
          </div>
        </dl>
      </div>
    </section>
  );
}

function Connector({
  id,
  tone,
  children,
}: {
  id: string;
  tone: LineTone | "soft";
  children: ReactNode;
}) {
  return (
    <span className={`conn conn-${id} line-${tone}`} aria-hidden="true">
      <span className="conn-line" />
      <span className="conn-head" />
      {children}
    </span>
  );
}

function Panel({
  id,
  state,
  scanning,
  ember,
  span,
  timeline,
  restartKey,
  children,
}: {
  id: PanelId;
  state: PanelState;
  scanning: boolean;
  ember: boolean;
  span: { start: number; end: number };
  timeline: Timeline;
  restartKey: string;
  children: ReactNode;
}) {
  const scan = useRef<HTMLSpanElement>(null);
  useProgress(scan, {
    active: scanning,
    start: span.start,
    end: span.end,
    running: timeline.running,
    position: timeline.position,
    restartKey,
  });
  return (
    <div className={`pp pp-${id} is-${state}`}>
      <span
        ref={scan}
        className={ember ? "scan is-ember" : "scan"}
        style={scanning ? undefined : { transform: "scaleX(0)" }}
        aria-hidden="true"
      />
      {children}
    </div>
  );
}

function PanelHead({
  icon,
  title,
  sub,
}: {
  icon: ReactNode;
  title: string;
  sub: string;
}) {
  return (
    <p className="pp-head">
      <b>
        <span className="pp-icon">{icon}</span>
        {title}
      </b>
      <span>{sub}</span>
    </p>
  );
}

function Foot({ on, children }: { on: boolean; children: ReactNode }) {
  return (
    <p className={on ? "pp-foot is-on" : "pp-foot"}>
      <Check size={12} />
      {children}
    </p>
  );
}

function PipelineStepButton({
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
  onPick: (k: number) => void;
}) {
  const fill = useRef<HTMLSpanElement>(null);
  const isCurrent = index === current;
  const span = pipelineStepSpan(index);
  useProgress(fill, {
    active: isCurrent,
    start: span.start,
    end: span.end,
    running: timeline.running,
    position: timeline.position,
    restartKey: `${current}:${timeline.epoch}`,
  });
  const ember = (index === 2 || index === 3) && index <= current;
  return (
    <button
      type="button"
      className="pstep"
      aria-current={isCurrent ? "step" : undefined}
      aria-label={`Step ${index + 1}: ${plain(title)}`}
      onClick={() => onPick(index)}
    >
      <span className="pstep-track">
        <span
          ref={fill}
          className={ember ? "pstep-fill is-ember" : "pstep-fill"}
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
