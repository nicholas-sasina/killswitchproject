import { useCallback, useEffect, useRef, useState } from "react";

export type TimelineOptions = {
  /** Every time (ms) the picture changes, ascending, starting at 0. */
  events: readonly number[];
  cycle: number;
  /** Allowed to run right now: on screen, tab visible, started. */
  active: boolean;
  initialT: number;
  initialPlaying: boolean;
};

export type Timeline = {
  /** The last event reached. React state changes only when this does. */
  t: number;
  /** What the person asked for with Pause / Play. */
  playing: boolean;
  /** playing and active: the clock is actually moving. */
  running: boolean;
  /** Counts seeks, so effects can tell a jump from a tick. */
  epoch: number;
  /** The precise position in ms, between events too. */
  position: () => number;
  seek: (t: number, play: boolean) => void;
  setPlaying: (playing: boolean) => void;
};

/**
 * A looping clock that sets state only at the given events. Everything that
 * moves between two events is CSS or WAAPI, synced through position().
 */
export function useTimeline({
  events,
  cycle,
  active,
  initialT,
  initialPlaying,
}: TimelineOptions): Timeline {
  const [t, setT] = useState(initialT);
  const [playing, setPlaying] = useState(initialPlaying);
  const [epoch, setEpoch] = useState(0);
  const running = playing && active;
  const anchor = useRef<{ t: number; at: number | null }>({
    t: initialT,
    at: null,
  });

  const position = useCallback(() => {
    const { t: base, at } = anchor.current;
    return at === null ? base : base + (performance.now() - at);
  }, []);

  useEffect(() => {
    if (!running) return;
    anchor.current = { t: anchor.current.t, at: performance.now() };
    let timer = 0;
    const schedule = () => {
      const now = position();
      const next = events.find((e) => e > now) ?? cycle;
      timer = window.setTimeout(
        () => {
          const reached = next >= cycle ? 0 : next;
          anchor.current = { t: reached, at: performance.now() };
          setT(reached);
          schedule();
        },
        Math.max(0, next - now),
      );
    };
    schedule();
    return () => {
      window.clearTimeout(timer);
      anchor.current = { t: position(), at: null };
    };
  }, [running, epoch, events, cycle, position]);

  const seek = useCallback((to: number, play: boolean) => {
    anchor.current = { t: to, at: null };
    setT(to);
    setPlaying(play);
    setEpoch((e) => e + 1);
  }, []);

  return { t, playing, running, epoch, position, seek, setPlaying };
}
