import { useEffect, useState, type RefObject } from "react";

const THRESHOLDS = Array.from({ length: 21 }, (_, i) => i / 20);

/**
 * inView: any part of the element is on screen.
 * reached: at some point, `share` of the element (or of the viewport, for
 * elements taller than it) was on screen. Latches once true.
 */
export function useInView(
  ref: RefObject<Element | null>,
  share = 0,
): { inView: boolean; reached: boolean } {
  const [state, setState] = useState({ inView: false, reached: false });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      setState({ inView: true, reached: true });
      return;
    }
    const io = new IntersectionObserver(
      ([entry]) => {
        const viewport = entry.rootBounds?.height ?? 0;
        const covered = Math.max(
          entry.intersectionRatio,
          viewport > 0 ? entry.intersectionRect.height / viewport : 0,
        );
        setState((prev) => {
          const inView = entry.isIntersecting;
          const reached = prev.reached || (inView && covered >= share);
          return prev.inView === inView && prev.reached === reached
            ? prev
            : { inView, reached };
        });
      },
      { threshold: THRESHOLDS },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [ref, share]);

  return state;
}
