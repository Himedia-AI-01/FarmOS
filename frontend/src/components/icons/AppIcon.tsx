// Bespoke FarmOS icon set — designed in-house to replace Material icons in the
// brand-defining nav surfaces (Sidebar + MobileNav). Hand-authored 24×24 line
// icons with a slight leafy/organic terminus, 1.6 stroke, round caps & joins.
//
// All shapes use `currentColor` so they recolor with text-color utilities.
// Optical adjustments are intentional: each glyph is balanced inside the 24-box
// rather than mathematically centered.

type IconProps = {
  className?: string;
  size?: number;
  strokeWidth?: number;
  'aria-label'?: string;
};

const baseProps = (
  className: string | undefined,
  size: number,
  sw: number,
  label?: string,
): React.SVGProps<SVGSVGElement> => ({
  width: size,
  height: size,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: sw,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  className,
  role: label ? 'img' : 'presentation',
  'aria-label': label,
  'aria-hidden': label ? undefined : true,
});

// ── Dashboard / Command — sprout in a small enclosure (the "home" stem) ────
export function IconCommand({ className, size = 24, strokeWidth = 1.6, ...rest }: IconProps) {
  return (
    <svg {...baseProps(className, size, strokeWidth, rest['aria-label'])}>
      <path d="M4 11.5 12 4l8 7.5" />
      <path d="M6 10.5V20h12v-9.5" />
      <path d="M12 20v-5.5" />
      <path d="M12 14.5c-1.6 0-2.4-1-2.4-2.5 1.6 0 2.4 1 2.4 2.5z" fill="currentColor" opacity="0.18" />
      <path d="M12 14.5c1.6 0 2.4-1 2.4-2.5-1.6 0-2.4 1-2.4 2.5z" fill="currentColor" opacity="0.18" />
    </svg>
  );
}

// ── IoT / Sensors — soil stake with a signal arc ──────────────────────────
export function IconSensors({ className, size = 24, strokeWidth = 1.6, ...rest }: IconProps) {
  return (
    <svg {...baseProps(className, size, strokeWidth, rest['aria-label'])}>
      <path d="M12 14V5" />
      <circle cx="12" cy="4" r="1.4" fill="currentColor" />
      <path d="M9 17h6" />
      <path d="M6 19c1.6-1.4 3.7-2.2 6-2.2s4.4.8 6 2.2" />
      <path d="M3.5 21c2.2-2 5.2-3.2 8.5-3.2s6.3 1.2 8.5 3.2" opacity="0.55" />
    </svg>
  );
}

// ── Diagnosis — leaf under magnifier ──────────────────────────────────────
export function IconDiagnosis({ className, size = 24, strokeWidth = 1.6, ...rest }: IconProps) {
  return (
    <svg {...baseProps(className, size, strokeWidth, rest['aria-label'])}>
      <path d="M5 14.5C5 9 9 5 14.5 5c0 5.5-4 9.5-9.5 9.5z" />
      <path d="M6 13.5c2.4-1.6 4.4-3.6 6-6" opacity="0.6" />
      <circle cx="14.5" cy="14.5" r="3.6" />
      <path d="M17.2 17.2 20.5 20.5" />
    </svg>
  );
}

// ── Journal — open notebook with a leaf bookmark ──────────────────────────
export function IconJournal({ className, size = 24, strokeWidth = 1.6, ...rest }: IconProps) {
  return (
    <svg {...baseProps(className, size, strokeWidth, rest['aria-label'])}>
      <path d="M4 6.2c2.6-.8 5.4-.8 8 0v12.5c-2.6-.8-5.4-.8-8 0V6.2z" />
      <path d="M20 6.2c-2.6-.8-5.4-.8-8 0v12.5c2.6-.8 5.4-.8 8 0V6.2z" />
      <path d="M14.5 4.2c.6 1.4 1.6 2.2 2.8 2.4" opacity="0.7" />
      <path d="M17.3 6.6c-.4 1.2-1.2 2.1-2.4 2.5" opacity="0.7" />
    </svg>
  );
}

// ── Weather — cloud with sun and a single drop ────────────────────────────
export function IconWeather({ className, size = 24, strokeWidth = 1.6, ...rest }: IconProps) {
  return (
    <svg {...baseProps(className, size, strokeWidth, rest['aria-label'])}>
      <circle cx="17" cy="7" r="2.6" opacity="0.7" />
      <path d="M17 2.6V4M22 7h-1.4M19.6 3.4l-1 1M21.4 11.4 20.4 10.4" opacity="0.6" />
      <path d="M5 16c0-2.5 2.1-4.5 4.7-4.5 1.7 0 3.2.9 4 2.3 1.6.1 3.3 1.4 3.3 3.4 0 1.8-1.5 3.3-3.5 3.3H7.5C6.1 20.5 5 19 5 17.4 5 16.9 5 16.4 5 16z" />
      <path d="M11 22.5l1-2" opacity="0.85" />
    </svg>
  );
}

// ── Market — balance scale tipping toward the right (price/coin side) ────
export function IconMarket({ className, size = 24, strokeWidth = 1.6, ...rest }: IconProps) {
  return (
    <svg {...baseProps(className, size, strokeWidth, rest['aria-label'])}>
      <path d="M12 4v15" />
      <path d="M9 19h6" />
      <path d="M5 6h14" />
      <path d="M5 6 3 12.5c0 1.4 1 2.4 2.5 2.4S8 13.9 8 12.5L5 6z" />
      <path d="M19 6l-2 7c0 1.4 1 2.4 2.5 2.4s2.5-1 2.5-2.4L19 6z" />
      <circle cx="12" cy="4" r="0.8" fill="currentColor" />
    </svg>
  );
}

// ── Subsidy — official document with a small wax seal ────────────────────
export function IconSubsidy({ className, size = 24, strokeWidth = 1.6, ...rest }: IconProps) {
  return (
    <svg {...baseProps(className, size, strokeWidth, rest['aria-label'])}>
      <path d="M6 3h9l4 4v14H6z" />
      <path d="M15 3v4h4" />
      <path d="M9 11h6M9 14.5h6M9 18h4" opacity="0.85" />
      <circle cx="17.5" cy="17.5" r="1.7" fill="currentColor" opacity="0.85" />
    </svg>
  );
}

// ── Reviews — speech bubble with a star ──────────────────────────────────
export function IconReviews({ className, size = 24, strokeWidth = 1.6, ...rest }: IconProps) {
  return (
    <svg {...baseProps(className, size, strokeWidth, rest['aria-label'])}>
      <path d="M4 6.5C4 5 5 4 6.5 4h11C19 4 20 5 20 6.5v8c0 1.5-1 2.5-2.5 2.5H10l-4 4v-4H6.5C5 17 4 16 4 14.5v-8z" />
      <path d="m12 7.5 1.4 2.8 3.1.4-2.3 2.1.6 3-2.8-1.5-2.8 1.5.6-3-2.3-2.1 3.1-.4z" fill="currentColor" opacity="0.92" stroke="none" />
    </svg>
  );
}

// ── Agent — four-pointed sparkle (Auto-Awesome equivalent, leafier) ──────
export function IconAgent({ className, size = 24, strokeWidth = 1.6, ...rest }: IconProps) {
  return (
    <svg {...baseProps(className, size, strokeWidth, rest['aria-label'])}>
      <path
        d="M12 2.5c.7 3.6 2.4 5.3 6 6-3.6.7-5.3 2.4-6 6-.7-3.6-2.4-5.3-6-6 3.6-.7 5.3-2.4 6-6z"
        fill="currentColor"
        stroke="none"
      />
      <path d="M19 14c.3 1.6 1 2.3 2.5 2.6-1.5.3-2.2 1-2.5 2.6-.3-1.6-1-2.3-2.5-2.6 1.5-.3 2.2-1 2.5-2.6z" fill="currentColor" stroke="none" opacity="0.7" />
    </svg>
  );
}

// ── Profile — operator portrait silhouette ───────────────────────────────
export function IconProfile({ className, size = 24, strokeWidth = 1.6, ...rest }: IconProps) {
  return (
    <svg {...baseProps(className, size, strokeWidth, rest['aria-label'])}>
      <circle cx="12" cy="8.5" r="3.5" />
      <path d="M5 20.5c0-3.6 3.1-6.5 7-6.5s7 2.9 7 6.5" />
    </svg>
  );
}

// ── Sign-out — door with arrow (slightly hand-drawn) ─────────────────────
export function IconSignOut({ className, size = 24, strokeWidth = 1.6, ...rest }: IconProps) {
  return (
    <svg {...baseProps(className, size, strokeWidth, rest['aria-label'])}>
      <path d="M10 4H5v16h5" />
      <path d="M14 8l4 4-4 4" />
      <path d="M18 12h-9" />
    </svg>
  );
}

// ── More-horiz — three dots ─────────────────────────────────────────────
export function IconMore({ className, size = 24, strokeWidth = 1.6, ...rest }: IconProps) {
  return (
    <svg {...baseProps(className, size, strokeWidth, rest['aria-label'])}>
      <circle cx="6" cy="12" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
      <circle cx="18" cy="12" r="1.4" fill="currentColor" stroke="none" />
    </svg>
  );
}
