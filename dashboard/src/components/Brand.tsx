/**
 * The Burn mark.
 *
 * Two brackets holding a flame. The brackets are the product's actual claim:
 * the fire is contained, not merely lit. They are also the same brackets the
 * interface uses for severity tokens ([CRIT], [HIGH]), so the mark and the
 * data share one vocabulary instead of the logo being decoration.
 *
 * Drawn on a 32 unit grid with a 3.2 unit stem so the silhouette survives at
 * 16px, where a monoline flame turns to mush. `BurnTile` is the filled variant
 * for the favicon, where a solid shape reads better than an outline.
 */

export function BurnMark({ title }: { title?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      {/* Containment brackets, left and right. */}
      <path
        fill="currentColor"
        d="M2.5 2.5h8.5v3.2H5.7v20.6h5.3v3.2H2.5v-27Zm18.5 0h8.5v27H21v-3.2h5.3V5.7H21V2.5Z"
      />
      {/* Flame, optically centred between the brackets. */}
      <path
        fill="currentColor"
        d="M16 7.8c2.1 3.2.8 4.6 1.8 5.6.8.9 1.9-.2 2.1-1.3 1.8 1.8 2.6 3.6 2.6 5.5 0 3.1-2.8 5.6-6.5 5.6s-6.5-2.5-6.5-5.6c0-2.6 1.5-4.4 3-6.1 1.5-1.8 2.8-2.5 3.5-3.7Z"
      />
    </svg>
  );
}

/** Solid tile variant: used for the favicon and any small app-icon context. */
export function BurnTile() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <path fill="currentColor" d="M3 3h26v22.2L25.2 29H3V3Z" />
      <path
        fill="#0b0a09"
        d="M16.2 8c2.2 3.4.8 4.9 1.9 6 .9.9 2-.2 2.2-1.4 1.9 1.9 2.8 3.8 2.8 5.8 0 3.3-3 5.9-6.9 5.9s-6.9-2.6-6.9-5.9c0-2.7 1.6-4.6 3.2-6.4 1.6-1.9 3-2.7 3.7-4Z"
      />
    </svg>
  );
}

/** Mark plus wordmark. The letterspacing is part of the mark, not a flourish. */
export function Brand() {
  return (
    <a href="#/overview" className="brand" aria-label="Burn home">
      <span className="brand-mark">
        <BurnMark />
      </span>
      <span className="brand-word">BURN</span>
    </a>
  );
}
