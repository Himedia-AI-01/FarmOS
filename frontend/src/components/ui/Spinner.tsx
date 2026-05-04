import { cn } from '@/lib/cn';

interface SpinnerProps {
  size?: number;
  className?: string;
  /** Screen-reader label. Empty string suppresses. */
  label?: string;
  tone?: 'primary' | 'inverse' | 'mute';
}

const TONE = {
  primary: 'text-[color:var(--color-primary)]',
  inverse: 'text-white',
  mute: 'text-[color:var(--color-ink-mute)]',
};

export default function Spinner({ size = 18, className, label = '로딩 중', tone = 'primary' }: SpinnerProps) {
  return (
    <span
      role="status"
      aria-live="polite"
      className={cn('inline-flex items-center justify-center', TONE[tone], className)}
    >
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden
        className="motion-safe:animate-spin"
        style={{ animationDuration: '850ms' }}
      >
        <circle cx="12" cy="12" r="9.5" stroke="currentColor" strokeOpacity="0.18" strokeWidth="2.4" />
        <path
          d="M12 2.5a9.5 9.5 0 0 1 9.5 9.5"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
        />
      </svg>
      {label && <span className="sr-only">{label}</span>}
    </span>
  );
}
