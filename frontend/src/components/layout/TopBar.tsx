import { useNavigate } from 'react-router-dom';
import { MdAutoAwesome, MdLogout } from 'react-icons/md';
import { useAuth } from '@/context/AuthContext';
import { useFarmAgentContext } from '@/context/FarmAgentContext';
import { cn } from '@/lib/cn';

interface TopBarProps {
  title: string;
  onOpenAgent: () => void;
}

export default function TopBar({ title, onOpenAgent }: TopBarProps) {
  const { user, logout } = useAuth();
  const { busy, messages } = useFarmAgentContext();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  const unread = messages.filter((m) => m.role === 'assistant').length;

  return (
    <header className="sticky top-0 z-30 h-16 border-b border-[color:var(--color-line-soft)] bg-[color:var(--color-surface)]/88 backdrop-blur-md sm:h-[68px]">
      <div className="flex h-full items-center justify-between gap-3 px-4 sm:px-6 lg:px-8">
        <h1 className="min-w-0 truncate text-[1.25rem] font-bold leading-none tracking-[-0.024em] text-[color:var(--color-ink)] sm:text-[1.4375rem]">
          {title}
        </h1>

        <div className="flex items-center gap-1.5 sm:gap-2">
          <button
            type="button"
            onClick={onOpenAgent}
            aria-label={busy ? 'Farm Agent — 분석 중' : 'Farm Agent 열기'}
            aria-haspopup="dialog"
            aria-live={busy ? 'polite' : undefined}
            className={cn(
              'relative inline-flex h-10 items-center gap-2 rounded-full border px-3.5 text-[14px] font-semibold transition-all duration-200 ease-out 2xl:hidden',
              busy
                ? 'border-transparent bg-[color:var(--color-primary)] text-white shadow-[var(--shadow-sm)]'
                : 'border-[color:var(--color-line)] bg-[color:var(--color-card)] text-[color:var(--color-ink)] hover:border-[color:var(--color-primary)] hover:bg-[color:var(--color-primary-soft)] hover:text-[color:var(--color-primary-dark)]',
            )}
          >
            <MdAutoAwesome
              aria-hidden
              className={cn(
                'text-[18px]',
                busy ? 'motion-safe:animate-pulse' : 'text-[color:var(--color-primary)]',
              )}
            />
            <span className="hidden sm:inline">{busy ? '분석 중' : 'Agent'}</span>
            {unread > 0 && !busy && (
              <span
                aria-label={`읽지 않은 응답 ${unread}건`}
                className="absolute -right-1 -top-1 flex h-[18px] min-w-[18px] items-center justify-center rounded-full border-2 border-[color:var(--color-surface)] bg-[color:var(--color-primary)] px-1 text-[10.5px] font-bold leading-none text-white"
              >
                {unread > 9 ? '9+' : unread}
              </span>
            )}
          </button>

          {user && (
            <div className="flex items-center gap-1.5 sm:gap-2.5">
              <div className="hidden text-right leading-tight md:block">
                <p className="text-[14px] font-bold text-[color:var(--color-ink)]">{user.name}님</p>
                <p className="mt-0.5 max-w-[200px] truncate text-[12px] text-[color:var(--color-ink-mute)]">
                  {user.farmname || user.main_crop || user.user_id}
                </p>
              </div>
              <button
                type="button"
                onClick={handleLogout}
                className="icon-btn icon-btn--danger"
                aria-label="로그아웃"
              >
                <MdLogout aria-hidden className="text-[19px]" />
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
