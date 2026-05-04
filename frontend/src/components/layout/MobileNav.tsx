import { useEffect, useRef, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { useAuth } from '@/context/AuthContext';
import {
  IconAgent,
  IconCommand,
  IconDiagnosis,
  IconJournal,
  IconMarket,
  IconMore,
  IconReviews,
  IconSensors,
  IconSignOut,
  IconSubsidy,
  IconWeather,
} from '@/components/icons/AppIcon';
import { cn } from '@/lib/cn';

interface MobileNavProps {
  onOpenAgent: () => void;
}

const MAIN_TABS = [
  { to: '/', icon: IconCommand, label: '커맨드' },
  { to: '/iot', icon: IconSensors, label: '제어' },
  { to: '/diagnosis', icon: IconDiagnosis, label: '진단' },
  { to: '/journal', icon: IconJournal, label: '일지' },
];

const MORE_TABS = [
  { to: '/weather', icon: IconWeather, label: '기상' },
  { to: '/market', icon: IconMarket, label: '시세' },
  { to: '/subsidy', icon: IconSubsidy, label: '직불' },
  { to: '/reviews', icon: IconReviews, label: '리뷰' },
];

export default function MobileNav({ onOpenAgent }: MobileNavProps) {
  const [showMore, setShowMore] = useState(false);
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const sheetRef = useRef<HTMLDivElement>(null);

  const handleLogout = async () => {
    setShowMore(false);
    await logout();
    navigate('/login');
  };

  // ESC closes the more-sheet
  useEffect(() => {
    if (!showMore) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setShowMore(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [showMore]);

  return (
    <>
      <AnimatePresence>
        {showMore && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.16 }}
            className="fixed inset-0 z-50 lg:hidden"
            onClick={() => setShowMore(false)}
            role="presentation"
          >
            <div className="absolute inset-0 bg-[color:var(--color-ink)]/40 backdrop-blur-[3px]" />
            <motion.div
              ref={sheetRef}
              role="dialog"
              aria-modal="true"
              aria-label="더 보기"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
              transition={{ type: 'spring', stiffness: 380, damping: 32 }}
              className="absolute bottom-[88px] left-3 right-3 rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-card)] p-3 shadow-[var(--shadow-lg)]"
              onClick={(event) => event.stopPropagation()}
            >
              <NavLink
                to="/profile"
                onClick={() => setShowMore(false)}
                className="mb-2.5 flex items-center gap-3 rounded-xl bg-[color:var(--color-surface)] p-3"
              >
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[color:var(--color-primary-soft)] text-[color:var(--color-primary-dark)]">
                  <IconAgent size={20} aria-hidden />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[14px] font-bold text-[color:var(--color-ink)]">{user?.name}</p>
                  <p className="truncate text-[12px] text-[color:var(--color-ink-mute)]">
                    {user?.farmname || user?.main_crop || user?.user_id}
                  </p>
                </div>
              </NavLink>

              <ul className="grid grid-cols-4 gap-1.5">
                {MORE_TABS.map(({ to, icon: Icon, label }) => (
                  <li key={to}>
                    <NavLink
                      to={to}
                      onClick={() => setShowMore(false)}
                      className={({ isActive }) =>
                        cn(
                          'flex min-h-[60px] flex-col items-center justify-center gap-1 rounded-xl px-2 py-2.5 text-[12px] font-semibold transition',
                          isActive
                            ? 'bg-[color:var(--color-primary)] text-white'
                            : 'bg-[color:var(--color-surface)] text-[color:var(--color-ink-soft)] hover:bg-[color:var(--color-primary-soft)] hover:text-[color:var(--color-primary-dark)]',
                        )
                      }
                    >
                      <Icon aria-hidden size={22} />
                      {label}
                    </NavLink>
                  </li>
                ))}
              </ul>

              <button
                type="button"
                onClick={handleLogout}
                className="mt-2.5 flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl py-2.5 text-[14px] font-semibold text-[color:var(--color-ink-mute)] hover:bg-[color:var(--color-danger-light)] hover:text-[color:var(--color-danger)]"
              >
                <IconSignOut aria-hidden size={17} />
                로그아웃
              </button>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      <nav
        aria-label="하단 메뉴"
        className="fixed bottom-0 left-0 right-0 z-50 grid h-[78px] grid-cols-6 border-t border-[color:var(--color-line)] bg-[color:var(--color-card)]/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-md lg:hidden"
      >
        {MAIN_TABS.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              cn(
                'relative flex flex-col items-center justify-center gap-1 text-[11.5px] font-semibold transition-colors',
                isActive ? 'text-[color:var(--color-primary)]' : 'text-[color:var(--color-ink-mute)]',
              )
            }
          >
            {({ isActive }) => (
              <>
                {isActive && (
                  <span
                    aria-hidden
                    className="absolute top-0 h-[3px] w-8 rounded-b-full bg-[color:var(--color-primary)]"
                  />
                )}
                <Icon aria-hidden size={22} strokeWidth={isActive ? 1.95 : 1.6} />
                {label}
              </>
            )}
          </NavLink>
        ))}
        <button
          type="button"
          onClick={onOpenAgent}
          aria-label="Farm Agent 열기"
          aria-haspopup="dialog"
          className="flex flex-col items-center justify-center gap-1 text-[11.5px] font-bold text-[color:var(--color-primary-dark)]"
        >
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-[color:var(--color-primary)] text-white shadow-[var(--shadow-sm)]">
            <IconAgent aria-hidden size={20} />
          </span>
          Agent
        </button>
        <button
          type="button"
          onClick={() => setShowMore((value) => !value)}
          aria-label="더 보기"
          aria-expanded={showMore}
          aria-haspopup="dialog"
          className={cn(
            'relative flex flex-col items-center justify-center gap-1 text-[11.5px] font-semibold transition-colors',
            showMore ? 'text-[color:var(--color-primary)]' : 'text-[color:var(--color-ink-mute)]',
          )}
        >
          {showMore && (
            <span
              aria-hidden
              className="absolute top-0 h-[3px] w-8 rounded-b-full bg-[color:var(--color-primary)]"
            />
          )}
          <IconMore aria-hidden size={23} />
          더보기
        </button>
      </nav>
    </>
  );
}
