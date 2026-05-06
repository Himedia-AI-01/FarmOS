import { useEffect, useId, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { MdClose } from 'react-icons/md';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { useScrollLock } from '@/hooks/useScrollLock';
import { cn } from '@/lib/cn';

type Size = 'sm' | 'md' | 'lg' | 'xl' | 'full';

const SIZE: Record<Size, string> = {
  sm: 'max-w-[420px]',
  md: 'max-w-[560px]',
  lg: 'max-w-[760px]',
  xl: 'max-w-[960px]',
  full: 'max-w-[min(1200px,96vw)]',
};

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: Size;
  /** Disable backdrop click-to-close (still closes on Escape). */
  disableBackdropClose?: boolean;
  /** Hide the default close button in the header. */
  hideCloseButton?: boolean;
  /** Override the labelledby id (rarely needed). */
  ariaLabelledby?: string;
  /** Class added to the dialog panel. */
  panelClassName?: string;
  /** Class added to the body wrapper. */
  bodyClassName?: string;
}

export default function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = 'md',
  disableBackdropClose,
  hideCloseButton,
  ariaLabelledby,
  panelClassName,
  bodyClassName,
}: ModalProps) {
  const titleId = useId();
  const descId = useId();
  const trapRef = useFocusTrap<HTMLDivElement>(open);
  useScrollLock(open);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (typeof document === 'undefined') return null;

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.16, ease: 'easeOut' }}
          className="fixed inset-0 z-[80] flex items-end justify-center bg-[color:var(--color-ink)]/45 backdrop-blur-sm sm:items-center sm:p-6"
          role="presentation"
        >
          <button
            type="button"
            tabIndex={-1}
            aria-hidden
            className="absolute inset-0 cursor-default"
            onClick={() => {
              if (!disableBackdropClose) onClose();
            }}
          />
          <motion.div
            ref={trapRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={ariaLabelledby ?? (title ? titleId : undefined)}
            aria-describedby={description ? descId : undefined}
            tabIndex={-1}
            initial={{ opacity: 0, y: 24, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.985 }}
            transition={{ type: 'spring', stiffness: 380, damping: 32 }}
            className={cn(
              'relative flex w-full flex-col overflow-hidden rounded-t-2xl border border-[color:var(--color-line)] bg-[color:var(--color-card)] shadow-[0_24px_60px_-30px_rgba(31,92,61,0.35)] sm:rounded-2xl',
              SIZE[size],
              panelClassName,
            )}
          >
            {(title || !hideCloseButton) && (
              <header className="flex items-start gap-3 border-b border-[color:var(--color-line-soft)] px-5 py-4 sm:px-6">
                <div className="min-w-0 flex-1">
                  {title && (
                    <h2 id={titleId} className="text-[1.0625rem] font-bold leading-tight text-[color:var(--color-ink)]">
                      {title}
                    </h2>
                  )}
                  {description && (
                    <p id={descId} className="mt-1 text-[0.875rem] text-[color:var(--color-ink-mute)]">
                      {description}
                    </p>
                  )}
                </div>
                {!hideCloseButton && (
                  <button
                    type="button"
                    onClick={onClose}
                    aria-label="닫기"
                    className="-mr-1 -mt-1 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-[color:var(--color-ink-mute)] transition hover:bg-[color:var(--color-surface)] hover:text-[color:var(--color-ink)]"
                  >
                    <MdClose className="text-[20px]" />
                  </button>
                )}
              </header>
            )}
            <div className={cn('min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-6', bodyClassName)}>
              {children}
            </div>
            {footer && (
              <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-[color:var(--color-line-soft)] bg-[color:var(--color-surface)] px-5 py-3 sm:px-6">
                {footer}
              </footer>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
