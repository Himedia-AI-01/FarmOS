import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

interface EmptyStateProps {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  /** Compact variant — used inside cards. */
  compact?: boolean;
  className?: string;
}

export default function EmptyState({ icon, title, description, action, compact, className }: EmptyStateProps) {
  return (
    <div
      role="status"
      className={cn(
        'flex flex-col items-center justify-center text-center',
        compact ? 'gap-2 py-6' : 'gap-3 py-12',
        className,
      )}
    >
      {icon && (
        <div
          aria-hidden
          className={cn(
            'flex items-center justify-center rounded-full bg-[color:var(--color-primary-soft)] text-[color:var(--color-primary-dark)]',
            compact ? 'h-11 w-11' : 'h-14 w-14 mb-1',
          )}
        >
          {icon}
        </div>
      )}
      <h3 className={cn('font-bold text-[color:var(--color-ink)]', compact ? 'text-[0.9375rem]' : 'text-[1.0625rem]')}>
        {title}
      </h3>
      {description && (
        <p className={cn('max-w-[42ch] text-[color:var(--color-ink-mute)]', compact ? 'text-[0.8125rem]' : 'text-[0.9375rem]')}>
          {description}
        </p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
