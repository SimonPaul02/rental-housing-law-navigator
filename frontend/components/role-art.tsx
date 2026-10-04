import type { ReactNode } from "react";
import type { Role } from "@/lib/roles";

/** A picture of the app behind each of the four roles.
 *
 * Drawn rather than screenshotted. A screenshot of a real dashboard goes stale
 * the first time a column moves, needs four of them kept in step, and at the
 * size these render at it would be an unreadable grey smear anyway. What a
 * reviewer needs from a thumbnail is the *shape* of the view they are about to
 * be given — one building or six, a map or a document — and shape survives
 * being 100 pixels tall.
 *
 * So each plate says the one thing `Cardinality` says in words: the renter
 * gets a single home with the rules that reach it, the provider a roll-up over
 * a portfolio, the agency a map of stock that is nobody's address in
 * particular, the advocate a page with the quoted span and its citation. No
 * lettering, because lettering at this scale is either illegible or a lie
 * about what the screen says.
 *
 * Inline SVG for the same reasons as `brand.tsx`: it takes the palette from
 * the CSS variables, so the art cannot drift away from the design system the
 * way an exported PNG does, and it costs no request.
 *
 * `aria-hidden` throughout — the card already names the role and says what it
 * is in a sentence, and a screen reader does not need the drawing of it.
 */

/** The strip every plate is drawn on: 4:1, so a card can carry one without
 *  the credentials below it being pushed off the fold. */
const VIEW = "0 0 320 80";

function Plate({ id, children }: { id: string; children: ReactNode }) {
  return (
    <svg className="role-art" viewBox={VIEW} aria-hidden focusable="false">
      <defs>
        {/* The same 125° glass the surfaces use, mixed as paint rather than
            as a backdrop filter: an SVG cannot transmit what is behind it. */}
        <linearGradient id={`${id}-ground`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0.9" />
          <stop offset="0.55" stopColor="var(--glass-mint)" stopOpacity="0.3" />
          <stop offset="1" stopColor="var(--lavender)" stopOpacity="0.45" />
        </linearGradient>
      </defs>
      <rect
        x="0.5"
        y="0.5"
        width="319"
        height="79"
        rx="11"
        fill={`url(#${id}-ground)`}
        stroke="var(--glass-edge)"
      />
      {children}
    </svg>
  );
}

/** One home, and the rules that reach it.
 *
 * One lit window out of five, because a renter's subject is a flat and not a
 * building. The badge sits on the roofline rather than on the list: what is
 * promised is protection of this address, and the list beside it is what that
 * turns out to be — one rule open, the rest waiting. */
function RenterArt() {
  const windows: Array<[number, number, boolean]> = [
    [26, 38, false],
    [42, 38, true],
    [58, 38, false],
    [26, 52, false],
    [58, 52, false],
  ];
  const rules: Array<[number, number, boolean]> = [
    [10, 164, true],
    [32, 138, false],
    [54, 152, false],
  ];
  return (
    <Plate id="ra-renter">
      <rect x="14" y="71" width="72" height="2" rx="1" fill="var(--deep)" fillOpacity="0.12" />
      <rect
        x="20"
        y="32"
        width="56"
        height="38"
        rx="4"
        fill="#ffffff"
        fillOpacity="0.85"
        stroke="var(--deep)"
        strokeOpacity="0.16"
      />
      <path
        d="M14,34 L48,12 L82,34"
        fill="none"
        stroke="var(--deep)"
        strokeWidth="8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {windows.map(([x, y, lit]) => (
        <rect
          key={`${x}-${y}`}
          x={x}
          y={y}
          width="12"
          height="10"
          rx="2"
          fill={lit ? "var(--mint)" : "var(--evergreen)"}
          fillOpacity={lit ? 0.9 : 0.16}
        />
      ))}
      <rect x="42" y="52" width="12" height="18" rx="2" fill="var(--deep)" fillOpacity="0.22" />

      <circle cx="86" cy="23" r="11" fill="#ffffff" fillOpacity="0.95" />
      <circle cx="86" cy="23" r="8.5" fill="var(--mint)" />
      <path
        d="M82,23.2 L85,26.2 L90.4,19.6"
        fill="none"
        stroke="#ffffff"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />

      {rules.map(([top, width, open]) => (
        <g key={top}>
          {open && (
            <rect
              x="104"
              y={top}
              width="206"
              height="18"
              rx="6"
              fill="#ffffff"
              fillOpacity="0.62"
              stroke="var(--evergreen)"
              strokeOpacity="0.25"
            />
          )}
          <rect
            x="112"
            y={top + 3.5}
            width="11"
            height="11"
            rx="3"
            fill={open ? "var(--mint)" : "var(--deep)"}
            fillOpacity={open ? 0.9 : 0.12}
          />
          <rect
            x="130"
            y={top + 6}
            width={width}
            height="6"
            rx="3"
            fill="var(--deep)"
            fillOpacity={open ? 0.48 : 0.2}
          />
        </g>
      ))}
    </Plate>
  );
}

/** A portfolio, answered together.
 *
 * The bars come first and the buildings second, which is the whole claim: the
 * provider's page is the roll-up, and a building is the drill-down. Six tiles
 * carry four greens, one amber and one grey — the grey being a building whose
 * answer is blocked by a missing fact, which is a state this app shows rather
 * than guesses past. */
function ProviderArt() {
  const bars = [
    [20, 24],
    [36, 36],
    [52, 30],
    [68, 48],
  ];
  const tiles = [
    [112, 14, "var(--good)"],
    [180, 14, "var(--good)"],
    [248, 14, "var(--warn)"],
    [112, 44, "var(--good)"],
    [180, 44, "var(--deep)"],
    [248, 44, "var(--good)"],
  ] as const;
  return (
    <Plate id="ra-provider">
      <rect x="20" y="12" width="40" height="5" rx="2.5" fill="var(--deep)" fillOpacity="0.2" />
      {bars.map(([x, height], index) => (
        <rect
          key={x}
          x={x}
          y={68 - height}
          width="10"
          height={height}
          rx="5"
          fill={index === 3 ? "var(--mint)" : "var(--evergreen)"}
          fillOpacity={index === 3 ? 0.9 : 0.34 + index * 0.08}
        />
      ))}
      <rect x="16" y="69" width="68" height="2" rx="1" fill="var(--deep)" fillOpacity="0.14" />
      <rect x="96" y="12" width="1" height="56" fill="var(--deep)" fillOpacity="0.1" />

      {tiles.map(([x, y, tone]) => (
        <g key={`${x}-${y}`}>
          <rect
            x={x}
            y={y}
            width="58"
            height="26"
            rx="6"
            fill="#ffffff"
            fillOpacity="0.6"
            stroke="var(--deep)"
            strokeOpacity="0.14"
          />
          <rect
            x={x + 8}
            y={y + 8}
            width="10"
            height="10"
            rx="2"
            fill="var(--deep)"
            fillOpacity="0.24"
          />
          <rect
            x={x + 23}
            y={y + 9}
            width="22"
            height="4"
            rx="2"
            fill="var(--deep)"
            fillOpacity="0.2"
          />
          <rect
            x={x + 23}
            y={y + 16}
            width="14"
            height="3"
            rx="1.5"
            fill="var(--deep)"
            fillOpacity="0.13"
          />
          <circle
            cx={x + 50}
            cy={y + 7}
            r="3"
            fill={tone}
            fillOpacity={tone === "var(--deep)" ? 0.22 : 0.85}
          />
        </g>
      ))}
    </Plate>
  );
}

/** The whole stock, none of it theirs.
 *
 * A map, because the agency's subject is coverage: which addresses fall in
 * which jurisdiction and where the record runs out. One point is ringed, not
 * because it is saved — they save none — but because a map is how they reach
 * a single address at all. The meters are coverage, and the shortest one is
 * the point of the view. */
function AgencyArt() {
  const points = [
    [34, 44],
    [48, 32],
    [62, 52],
    [76, 38],
    [88, 58],
    [128, 30],
    [142, 48],
    [158, 24],
    [168, 58],
    [182, 40],
    [196, 54],
  ];
  const meters = [
    [26, 62, "var(--evergreen)", 0.78],
    [40, 44, "var(--mint)", 0.95],
    [54, 26, "var(--warn)", 0.6],
  ] as const;
  return (
    <Plate id="ra-agency">
      <defs>
        <clipPath id="ra-agency-canvas">
          <rect x="12" y="10" width="200" height="60" rx="8" />
        </clipPath>
      </defs>
      <rect
        x="12"
        y="10"
        width="200"
        height="60"
        rx="8"
        fill="#ffffff"
        fillOpacity="0.5"
        stroke="var(--deep)"
        strokeOpacity="0.12"
      />
      <g clipPath="url(#ra-agency-canvas)">
        <polygon
          points="8,36 54,16 98,26 110,54 70,74 20,66"
          fill="var(--glass-mint)"
          fillOpacity="0.6"
          stroke="var(--evergreen)"
          strokeOpacity="0.35"
        />
        <polygon
          points="110,54 98,26 152,8 200,20 218,48 186,76 132,74"
          fill="var(--lavender)"
          fillOpacity="0.55"
          stroke="var(--accent)"
          strokeOpacity="0.28"
        />
        <path
          d="M98,26 L110,54"
          stroke="var(--deep)"
          strokeOpacity="0.3"
          strokeWidth="1.2"
          strokeDasharray="3 3"
        />
        {points.map(([x, y]) => (
          <circle key={`${x}-${y}`} cx={x} cy={y} r="2.3" fill="var(--deep)" fillOpacity="0.45" />
        ))}
        <circle cx="112" cy="40" r="7" fill="none" stroke="var(--evergreen)" strokeOpacity="0.45" />
        <circle cx="112" cy="40" r="3.6" fill="var(--evergreen)" />
      </g>

      <rect x="224" y="12" width="44" height="5" rx="2.5" fill="var(--deep)" fillOpacity="0.22" />
      {meters.map(([y, width, tone, opacity]) => (
        <g key={y}>
          <rect x="224" y={y} width="82" height="6" rx="3" fill="var(--deep)" fillOpacity="0.1" />
          <rect x="224" y={y} width={width} height="6" rx="3" fill={tone} fillOpacity={opacity} />
        </g>
      ))}
    </Plate>
  );
}

/** The record behind the answer.
 *
 * Two pages, because an advocate's addresses are cases that never add up, and
 * the top one is open: the span quoted from the source is lit, the citation
 * sits under it ready to be pasted into a letter, and the amber pill is a
 * conflict this app will not resolve on its own. */
function AdvocateArt() {
  return (
    <Plate id="ra-advocate">
      <rect
        x="30"
        y="9"
        width="84"
        height="54"
        rx="6"
        fill="#ffffff"
        fillOpacity="0.55"
        stroke="var(--deep)"
        strokeOpacity="0.12"
      />
      <rect
        x="16"
        y="16"
        width="84"
        height="54"
        rx="6"
        fill="#ffffff"
        fillOpacity="0.94"
        stroke="var(--deep)"
        strokeOpacity="0.16"
      />
      {[
        [26, 56],
        [34, 64],
        [42, 44],
      ].map(([y, width]) => (
        <rect key={y} x="26" y={y} width={width} height="4" rx="2" fill="var(--deep)" fillOpacity="0.18" />
      ))}
      <rect x="24" y="48" width="68" height="13" rx="3" fill="var(--mint)" fillOpacity="0.32" />
      <rect x="24" y="48" width="3" height="13" rx="1.5" fill="var(--evergreen)" />
      <rect x="33" y="52.5" width="50" height="4" rx="2" fill="var(--deep)" fillOpacity="0.45" />
      <rect x="26" y="64" width="36" height="4" rx="2" fill="var(--deep)" fillOpacity="0.14" />

      <rect
        x="118"
        y="16"
        width="182"
        height="24"
        rx="7"
        fill="#ffffff"
        fillOpacity="0.72"
        stroke="var(--evergreen)"
        strokeOpacity="0.3"
      />
      <circle cx="131" cy="28" r="4.5" fill="var(--mint)" />
      <rect x="143" y="25" width="92" height="6" rx="3" fill="var(--deep)" fillOpacity="0.32" />
      <rect x="243" y="25" width="44" height="6" rx="3" fill="var(--deep)" fillOpacity="0.16" />

      <rect
        x="118"
        y="48"
        width="120"
        height="18"
        rx="6"
        fill="#ffffff"
        fillOpacity="0.5"
        stroke="var(--deep)"
        strokeOpacity="0.12"
      />
      {[
        [128, 14],
        [148, 24],
        [178, 18],
      ].map(([x, width]) => (
        <rect key={x} x={x} y="55" width={width} height="4" rx="2" fill="var(--deep)" fillOpacity="0.22" />
      ))}
      <rect
        x="246"
        y="48"
        width="54"
        height="18"
        rx="9"
        fill="var(--warn)"
        fillOpacity="0.16"
        stroke="var(--warn)"
        strokeOpacity="0.4"
      />
      <circle cx="259" cy="57" r="3.4" fill="var(--warn)" fillOpacity="0.85" />
      <rect x="268" y="55" width="22" height="4" rx="2" fill="var(--warn)" fillOpacity="0.5" />
    </Plate>
  );
}

const ART: Record<Role, () => ReactNode> = {
  renter: RenterArt,
  provider: ProviderArt,
  agency: AgencyArt,
  advocate: AdvocateArt,
};

/** The plate for one role. A `Record` rather than a switch, so adding a fifth
 *  role to `ROLE_SLUGS` fails to compile here instead of rendering nothing. */
export function RoleArt({ role }: { role: Role }) {
  const Art = ART[role];
  return <Art />;
}
