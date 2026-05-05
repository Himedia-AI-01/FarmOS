import { NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import {
  IconCommand,
  IconSensors,
  IconDiagnosis,
  IconJournal,
  IconWeather,
  IconMarket,
  IconSubsidy,
  IconReviews,
  IconProfile,
  IconSignOut,
} from '@/components/icons/AppIcon';
import { cn } from '@/lib/cn';

type NavItem = { to: string; label: string; icon: React.ElementType };

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: '운영',
    items: [
      { to: '/', label: '커맨드', icon: IconCommand },
      { to: '/iot', label: '시설 제어', icon: IconSensors },
      { to: '/diagnosis', label: '진단', icon: IconDiagnosis },
    ],
  },
  {
    label: '데이터',
    items: [
      { to: '/journal', label: '영농일지', icon: IconJournal },
      { to: '/weather', label: '기상', icon: IconWeather },
      { to: '/market', label: '시세', icon: IconMarket },
    ],
  },
  {
    label: '비즈니스',
    items: [
      { to: '/subsidy', label: '공익직불', icon: IconSubsidy },
      { to: '/reviews', label: '판매', icon: IconReviews },
    ],
  },
];

function LeafMark({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" fill="none" className={className} aria-hidden>
      <path d="M5 27c0-11 7-19 22-22-1 13-7 22-19 22-1 0-3 0-3 0z" fill="currentColor" opacity="0.92" />
      <path d="M7 25C13 18 19 13 26 9" stroke="white" strokeWidth="1.4" strokeLinecap="round" opacity="0.65" />
    </svg>
  );
}

export default function Sidebar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  return (
    <aside className="sticky top-0 flex h-screen w-[260px] flex-col border-r border-[color:var(--color-line)] bg-[color:var(--color-card)]">
      <div className="px-6 pt-7 pb-5">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[color:var(--color-primary)] text-white shadow-[var(--shadow-xs)]">
            <LeafMark className="h-5 w-5" />
          </span>
          <span className="text-[1.125rem] font-bold tracking-tight text-[color:var(--color-ink)]">
            FarmOS
          </span>
        </div>
      </div>

      <NavLink
        to="/profile"
        className={({ isActive }) =>
          cn(
            'mx-4 flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors',
            isActive
              ? 'bg-[color:var(--color-primary-soft)] text-[color:var(--color-primary-dark)]'
              : 'text-[color:var(--color-ink-soft)] hover:bg-[color:var(--color-surface)]',
          )
        }
        aria-label="농장 프로필"
      >
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[color:var(--color-surface-deep)] text-[color:var(--color-primary-dark)]">
          <IconProfile size={20} strokeWidth={1.7} />
        </div>
        <div className="min-w-0">
          <p className="truncate text-[14.5px] font-bold leading-tight text-[color:var(--color-ink)]">
            {user?.name ?? '농장 관리자'}
          </p>
          <p className="mt-0.5 truncate text-[12.5px] text-[color:var(--color-ink-mute)]">
            {user?.farmname || user?.main_crop || user?.user_id}
          </p>
        </div>
      </NavLink>

      <nav aria-label="주 메뉴" className="min-h-0 flex-1 overflow-y-auto px-4 pt-6 pb-2">
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="mb-6 last:mb-0">
            <p className="mb-1.5 px-3 text-[11px] font-semibold uppercase tracking-[0.06em] text-[color:var(--color-ink-faint)]">
              {group.label}
            </p>
            <ul className="space-y-0.5">
              {group.items.map(({ to, label, icon: Icon }) => (
                <li key={to}>
                  <NavLink
                    to={to}
                    end={to === '/'}
                    className={({ isActive }) =>
                      cn(
                        'group relative flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors',
                        isActive
                          ? 'bg-[color:var(--color-primary-soft)] text-[color:var(--color-primary-dark)]'
                          : 'text-[color:var(--color-ink-soft)] hover:bg-[color:var(--color-surface)] hover:text-[color:var(--color-ink)]',
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        {/* Left rail accent — quieter than the dot */}
                        <span
                          aria-hidden
                          className={cn(
                            'absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-[color:var(--color-primary)] transition-opacity',
                            isActive ? 'opacity-100' : 'opacity-0',
                          )}
                        />
                        <Icon
                          aria-hidden
                          size={20}
                          strokeWidth={isActive ? 1.85 : 1.6}
                          className={cn(
                            'shrink-0 transition-colors',
                            isActive
                              ? 'text-[color:var(--color-primary)]'
                              : 'text-[color:var(--color-ink-mute)] group-hover:text-[color:var(--color-primary)]',
                          )}
                        />
                        <span className="text-[14.5px] font-semibold">{label}</span>
                      </>
                    )}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="border-t border-[color:var(--color-line-soft)] px-4 py-3">
        <button
          type="button"
          onClick={handleLogout}
          className="flex w-full items-center justify-center gap-2 rounded-xl px-3 py-2.5 text-[14px] font-semibold text-[color:var(--color-ink-mute)] transition hover:bg-[color:var(--color-danger-light)] hover:text-[color:var(--color-danger)]"
        >
          <IconSignOut aria-hidden size={17} />
          로그아웃
        </button>
      </div>
    </aside>
  );
}
