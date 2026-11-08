import { useEffect, useRef, type RefObject } from "react";

// The hero's dot grid, on one canvas. It draws once and sleeps; the only
// time it runs requestAnimationFrame is the 1.4 s after a canary trip, when a
// ring of ember passes through the dots from the exposure ledger.

export type Mask = { cx: number; cy: number; rx: number; ry: number };

const SPACING = 18;
const RADIUS = 1.25;
const RIPPLE_RADIUS = 1.5;
const BUCKETS = 16;
const RIPPLE_MS = 1400;
/** How sharply the ring's leading edge rises, and how long its wake decays. */
const LEAD = 12;
const TAIL = 70;
const DOT = "#3f3f46";
const EMBER = "#e8522a";

type Field = {
  ripple: (origin: HTMLElement | null) => void;
  destroy: () => void;
};

function createField(canvas: HTMLCanvasElement, mask: Mask): Field | null {
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;

  let width = 0;
  let height = 0;
  let dpr = 1;
  let xs = new Float32Array(0);
  let ys = new Float32Array(0);
  let alpha = new Float32Array(0);
  let distance = new Float32Array(0);
  let baseLayer: HTMLCanvasElement | null = null;
  let raf = 0;
  let started = 0;
  let r0 = 0;
  let rMax = 0;

  // Groups dots by alpha so each frame is a handful of fills, not thousands.
  const paint = (
    target: CanvasRenderingContext2D,
    color: string,
    radius: number,
    alphaOf: (i: number) => number,
  ) => {
    const groups: number[][] = Array.from({ length: BUCKETS }, () => []);
    for (let i = 0; i < xs.length; i++) {
      const a = alphaOf(i);
      if (a <= 0.02) continue;
      groups[Math.min(BUCKETS - 1, Math.floor(a * BUCKETS))].push(i);
    }
    target.fillStyle = color;
    groups.forEach((group, b) => {
      if (!group.length) return;
      target.globalAlpha = (b + 1) / BUCKETS;
      target.beginPath();
      for (const i of group) {
        target.moveTo(xs[i] + radius, ys[i]);
        target.arc(xs[i], ys[i], radius, 0, Math.PI * 2);
      }
      target.fill();
    });
    target.globalAlpha = 1;
  };

  const drawBase = () => {
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (baseLayer) ctx.drawImage(baseLayer, 0, 0);
  };

  const layout = () => {
    width = canvas.clientWidth;
    height = canvas.clientHeight;
    dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);

    const mx = width * mask.cx;
    const my = height * mask.cy;
    const rx = width * mask.rx;
    const ry = height * mask.ry;
    const px: number[] = [];
    const py: number[] = [];
    const pa: number[] = [];
    for (let y = SPACING / 2; y < height; y += SPACING) {
      for (let x = SPACING / 2; x < width; x += SPACING) {
        const d = Math.hypot((x - mx) / rx, (y - my) / ry);
        const a = d <= 0.35 ? 1 : d >= 1 ? 0 : 1 - (d - 0.35) / 0.65;
        if (a <= 0.02) continue;
        px.push(x);
        py.push(y);
        pa.push(a);
      }
    }
    xs = Float32Array.from(px);
    ys = Float32Array.from(py);
    alpha = Float32Array.from(pa);

    baseLayer = document.createElement("canvas");
    baseLayer.width = canvas.width;
    baseLayer.height = canvas.height;
    const base = baseLayer.getContext("2d");
    if (base) {
      base.setTransform(dpr, 0, 0, dpr, 0, 0);
      paint(base, DOT, RADIUS, (i) => alpha[i]);
    }
    if (!raf) drawBase();
  };

  const frame = (now: number) => {
    const p = Math.min(1, (now - started) / RIPPLE_MS);
    const eased = 1 - (1 - p) * (1 - p);
    const r = r0 + (rMax - r0) * eased;
    const fade = p < 0.7 ? 1 : 1 - (p - 0.7) / 0.3;
    drawBase();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    paint(ctx, EMBER, RIPPLE_RADIUS, (i) => {
      const d = distance[i];
      if (d > r + LEAD || r - d > TAIL * 4) return 0;
      const heat = d > r ? 1 - (d - r) / LEAD : Math.exp(-(r - d) / TAIL);
      return Math.min(1, heat * alpha[i] * 1.3) * fade;
    });
    if (p < 1) {
      raf = requestAnimationFrame(frame);
    } else {
      raf = 0;
      drawBase();
    }
  };

  const ripple = (origin: HTMLElement | null) => {
    if (!origin || !xs.length) return;
    const box = canvas.getBoundingClientRect();
    const o = origin.getBoundingClientRect();
    // Undo any CSS scale on the plane: work in the canvas's own pixels.
    const k = box.width > 0 ? width / box.width : 1;
    const ox = (o.left + o.width / 2 - box.left) * k;
    const oy = (o.top + o.height / 2 - box.top) * k;
    distance = new Float32Array(xs.length);
    for (let i = 0; i < xs.length; i++)
      distance[i] = Math.hypot(xs[i] - ox, ys[i] - oy);
    r0 = (Math.min(o.width, o.height) / 2) * k * 0.85;
    rMax = Math.max(
      Math.hypot(ox, oy),
      Math.hypot(width - ox, oy),
      Math.hypot(ox, height - oy),
      Math.hypot(width - ox, height - oy),
    );
    started = performance.now();
    if (!raf) raf = requestAnimationFrame(frame);
  };

  layout();
  let pending = 0;
  const ro =
    typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => {
          if (pending) return;
          pending = requestAnimationFrame(() => {
            pending = 0;
            if (
              canvas.clientWidth !== width ||
              canvas.clientHeight !== height ||
              Math.min(2, window.devicePixelRatio || 1) !== dpr
            )
              layout();
          });
        });
  ro?.observe(canvas);

  return {
    ripple,
    destroy: () => {
      ro?.disconnect();
      cancelAnimationFrame(raf);
      cancelAnimationFrame(pending);
    },
  };
}

export function DotField({
  className,
  mask,
  burst,
  burstKey,
  originRef,
}: {
  className: string;
  mask: Mask;
  /** True on the canary-trip step: the ring fires when this turns on. */
  burst: boolean;
  /** Changes on every seek, so picking the step again fires it again. */
  burstKey: number;
  originRef: RefObject<HTMLElement | null>;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const field = useRef<Field | null>(null);
  const { cx, cy, rx, ry } = mask;

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const f = createField(canvas, { cx, cy, rx, ry });
    field.current = f;
    return () => {
      f?.destroy();
      field.current = null;
    };
  }, [cx, cy, rx, ry]);

  useEffect(() => {
    if (burst) field.current?.ripple(originRef.current);
  }, [burst, burstKey, originRef]);

  return <canvas ref={ref} className={className} aria-hidden="true" />;
}
