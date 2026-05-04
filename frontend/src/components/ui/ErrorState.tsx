import type { ReactNode } from 'react';
import { MdErrorOutline } from 'react-icons/md';
import { cn } from '@/lib/cn';

interface ErrorStateProps {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
  className?: string;
}

export default function ErrorState({
  title = '문제가 발생했어요',
  description,
  action,
  compact,
  className,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={cn(
        'flex flex-col items-center justify-center text-center',
        compact ? 'gap-2 py-6' : 'gap-3 py-12',
        className,
      )}
    >
      <div
        aria-hidden
        className={cn(
          'flex items-center justify-center rounded-full bg-[color:var(--color-danger-light)] text-[color:var(--color-danger)]',
          compact ? 'h-11 w-11' : 'h-14 w-14 mb-1',
        )}
      >
        <MdErrorOutline className={compact ? 'text-[22px]' : 'text-[26px]'} />
      </div>
      <h3 className={cn('font-bold text-[color:var(--color-ink)]', compact ? 'text-[0.9375rem]' : 'text-[1.0625rem]')}>
        {title}
      </h3>
      {description && (
        <p className={cn('max-w-[44ch] text-[color:var(--color-ink-mute)]', compact ? 'text-[0.8125rem]' : 'text-[0.9375rem]')}>
          {description}
        </p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
