/** The plua.ai logo, from the brand kit.
 *
 * Inlined rather than fetched from `public/`: the wordmark is a kilobyte of
 * geometry, it renders crisp at any size without a second request, and the
 * `.ai` keeps its own colour instead of being a flat image somebody later
 * recolours by accident. The files are in `public/brand/` all the same, for
 * the favicon and for anything that needs a URL.
 *
 * The geometry is the kit's, unchanged. `aria-hidden` on the marks because the
 * link or heading around them carries the name — a screen reader should hear
 * "plua.ai, home", not a path description.
 */

/** The full wordmark: the mark, `plua`, and `.ai` in its own grey. */
export function Wordmark({ height = 26 }: { height?: number }) {
  return (
    <svg
      viewBox="0 -24 300 102"
      height={height}
      width={(300 / 102) * height}
      fill="none"
      aria-hidden
      focusable="false"
    >
      <path
        d="M0,78 L0,26 A26,26 0 1 1 41.6,46.8 Z M12,26 a14,14 0 1 0 28,0 a14,14 0 1 0 -28,0 Z"
        fill="var(--deep)"
        fillRule="evenodd"
      />
      <circle cx="26" cy="26" r="5.6" fill="var(--mint)" />
      <g stroke="var(--deep)" strokeWidth="12" strokeLinecap="round">
        <path d="M68,-18 V46" />
        <path d="M90,6 V26 A20,20 0 0 0 130,26 M130,6 V46" />
        <circle cx="172" cy="26" r="20" />
        <path d="M192,6 V46" />
      </g>
      <circle cx="212" cy="46" r="6" fill="var(--ai)" />
      <g stroke="var(--ai)" strokeWidth="12" strokeLinecap="round">
        <circle cx="252" cy="26" r="20" />
        <path d="M272,6 V46" />
        <path d="M294,6 V46" />
      </g>
      <circle cx="294" cy="-18" r="6" fill="var(--ai)" />
    </svg>
  );
}

/** The mark alone — the `p` with the dot — for tight spots. */
export function Mark({ size = 22 }: { size?: number }) {
  return (
    <svg
      viewBox="-6 -6 64 90"
      height={size}
      width={(64 / 90) * size}
      fill="none"
      aria-hidden
      focusable="false"
    >
      <path
        d="M0,78 L0,26 A26,26 0 1 1 41.6,46.8 Z M12,26 a14,14 0 1 0 28,0 a14,14 0 1 0 -28,0 Z"
        fill="var(--deep)"
        fillRule="evenodd"
      />
      <circle cx="26" cy="26" r="5.6" fill="var(--mint)" />
    </svg>
  );
}
