import { cn } from '@/lib/cn';

type Tone = 'success' | 'warning' | 'danger' | 'info' | 'mute';

const TONE: Record<Tone, string> = {
  success: 'bg-[color:var(--color-success)]',
  warning: 'bg-[color:var(--color-warning)]',
  danger: 'bg-[color:var(--color-danger)]',
  info: 'bg-[color:var(--color-info)]',
  mute: 'bg-[color:var(--color-ink-faint)]',
};

interface StatusDotProps {
  tone?: Tone;
  pulse?: boolean;
  size?: number;
  className?: string;
  label?: string;
}

export default function StatusDot({ tone = 'success', pulse, size = 8, label, className }: StatusDotProps) {
  return (
    <span
      aria-label={label}
      role={label ? 'status' : undefined}
      className={cn(
        'inline-block shrink-0 rounded-full',
        TONE[tone],
        pulse && tone === 'success' && 'live-dot',
        className,
      )}
      style={{ width: size, height: size }}
    />
  );
}
