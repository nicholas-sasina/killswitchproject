import { useLayoutEffect, type RefObject } from "react";

type Plane = {
  width: number;
  /** Width of what must stay on screen, centred in the plane. */
  content: number;
  gutter: number;
};

/**
 * Scales a fixed-size plane to its frame with one transform. Writes --s
 * (scale) and --tx (centring offset, px) on the frame; CSS does the rest, so
 * resizing never re-renders React.
 */
export function usePlaneScale(
  ref: RefObject<HTMLElement | null>,
  { width, content, gutter }: Plane,
) {
  useLayoutEffect(() => {
    const frame = ref.current;
    if (!frame) return;
    const apply = (w: number) => {
      const s = Math.min(1, Math.max(0.1, (w - 2 * gutter) / content));
      frame.style.setProperty("--s", s.toFixed(4));
      frame.style.setProperty("--tx", `${((w - width * s) / 2).toFixed(2)}px`);
    };
    apply(frame.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => apply(entry.contentRect.width));
    ro.observe(frame);
    return () => ro.disconnect();
  }, [ref, width, content, gutter]);
}
