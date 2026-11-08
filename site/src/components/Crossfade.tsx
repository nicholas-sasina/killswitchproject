import { useState, type AnimationEvent, type ReactNode } from "react";

type Layer = { id: string; node: ReactNode };

/**
 * Swaps content when `id` changes: the new text settles in from 0.96 while the
 * old fades out in the same grid cell. With reduced motion it is an instant swap.
 */
export function Crossfade({
  id,
  children,
  className = "",
}: {
  id: string;
  children: ReactNode;
  className?: string;
}) {
  const [current, setCurrent] = useState<Layer>({ id, node: children });
  const [leaving, setLeaving] = useState<Layer | null>(null);
  const [changed, setChanged] = useState(false);

  if (current.id !== id) {
    setLeaving(current);
    setCurrent({ id, node: children });
    setChanged(true);
  }

  const done = (e: AnimationEvent) => {
    if (e.target === e.currentTarget) setLeaving(null);
  };

  return (
    <span className={`xf ${className}`}>
      <span key={id} className={changed ? "xf-layer xf-in" : "xf-layer"}>
        {children}
      </span>
      {leaving && (
        <span
          key={`out:${leaving.id}`}
          className="xf-layer xf-out"
          aria-hidden="true"
          onAnimationEnd={done}
        >
          {leaving.node}
        </span>
      )}
    </span>
  );
}
