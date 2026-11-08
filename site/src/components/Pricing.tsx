import { useId, useState, type ChangeEvent } from "react";
import {
  CALL_PRESETS,
  CALLS_MAX,
  DAYS_PER_MONTH,
  EXAMPLE_CALLS_PER_DAY,
  PRICE_PER_1000_CALLS_USD,
  SLIDER_STEPS,
  callsToSlider,
  formatCalls,
  formatUsd,
  monthlyCost,
  shortCalls,
  sliderToCalls,
} from "../content/pricing";
import { Crossfade } from "./Crossfade";

const PRICE = formatUsd(PRICE_PER_1000_CALLS_USD);

export function Pricing() {
  return (
    <section
      className="block wrap"
      id="pricing"
      aria-labelledby="pricing-title"
    >
      <div className="split block-rule">
        <h2 className="section-title" id="pricing-title">
          Priced per tool call
        </h2>
        <div className="split-body">
          <p className="price">{PRICE} for every 1,000 tool calls</p>
          <p className="body-lg">
            Swarms don&rsquo;t have a fixed number of agents, so Burn
            doesn&rsquo;t charge per agent. A swarm making{" "}
            {formatCalls(EXAMPLE_CALLS_PER_DAY)} tool calls a day comes to about{" "}
            {formatUsd(monthlyCost(EXAMPLE_CALLS_PER_DAY))} a month, with every
            protection included.
          </p>
          <Calculator />
        </div>
      </div>
    </section>
  );
}

function Calculator() {
  const id = useId();
  const [calls, setCalls] = useState(EXAMPLE_CALLS_PER_DAY);
  const [draft, setDraft] = useState(formatCalls(EXAMPLE_CALLS_PER_DAY));
  const [dragging, setDragging] = useState(false);

  const set = (next: number) => {
    setCalls(next);
    setDraft(formatCalls(next));
  };

  const onType = (e: ChangeEvent<HTMLInputElement>) => {
    const text = e.target.value;
    setDraft(text);
    const digits = text.replace(/[^\d]/g, "");
    if (digits) setCalls(Math.min(CALLS_MAX * 100, Number(digits)));
    else setCalls(0);
  };

  const cost = formatUsd(monthlyCost(calls));

  return (
    <div className="calc">
      <label className="calc-label" htmlFor={`${id}-calls`} id={`${id}-label`}>
        Tool calls per day
      </label>
      <div className="calc-row">
        <input
          id={`${id}-calls`}
          className="calc-input num"
          type="text"
          inputMode="numeric"
          autoComplete="off"
          value={draft}
          onChange={onType}
          onBlur={() => setDraft(formatCalls(calls))}
          aria-describedby={`${id}-out`}
        />
        <div className="calc-presets" role="group" aria-label="Examples">
          {CALL_PRESETS.map((preset) => (
            <button
              key={preset}
              type="button"
              className="calc-preset"
              aria-pressed={calls === preset}
              aria-label={`${formatCalls(preset)} a day`}
              onClick={() => set(preset)}
            >
              {shortCalls(preset)}
            </button>
          ))}
        </div>
      </div>
      <input
        className="calc-range"
        type="range"
        min={0}
        max={SLIDER_STEPS}
        step={1}
        value={callsToSlider(calls)}
        aria-labelledby={`${id}-label`}
        aria-valuetext={`${formatCalls(calls)} tool calls a day, ${cost} a month`}
        onChange={(e) => set(sliderToCalls(Number(e.target.value)))}
        onPointerDown={() => setDragging(true)}
        onPointerUp={() => setDragging(false)}
        onPointerCancel={() => setDragging(false)}
      />
      <p className="calc-out" id={`${id}-out`}>
        <span className="calc-cost num">
          {/* While dragging the number follows the thumb; it crossfades on release. */}
          <Crossfade id={dragging ? "dragging" : cost}>{cost}</Crossfade>
        </span>
        <span className="calc-per">a month</span>
      </p>
      <p className="calc-math num">
        {formatCalls(calls)} calls &times; {DAYS_PER_MONTH} days &times; {PRICE}{" "}
        per 1,000
      </p>
    </div>
  );
}
