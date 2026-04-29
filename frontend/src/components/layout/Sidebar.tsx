import { NavLink, useNavigate } from 'react-router-dom';
import {
  MdAgriculture,
  MdArticle,
  MdAutoAwesome,
  MdBugReport,
  MdCloud,
  MdDashboard,
  MdLogout,
  MdManageAccounts,
  MdPayments,
  MdReviews,
  MdSensors,
  MdShowChart,
  MdStorefront,
} from 'react-icons/md';
import { useAuth } from '@/context/AuthContext';

type NavItem = {
  to: string;
  label: string;
  detail: string;
  icon: React.ElementType;
};

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: 'Command',
    items: [
      { to: '/', label: '운영 커맨드', detail: '브리핑 · 우선순위', icon: MdDashboard },
      { to: '/iot', label: '시설 제어', detail: '센서 · 자동화', icon: MdSensors },
      { to: '/diagnosis', label: '진단 워크벤치', detail: '이미지 · 처방', icon: MdBugReport },
    ],
  },
  {
    label: 'Work',
    items: [
      { to: '/journal', label: '영농 기록', detail: '음성 · 통합일지', icon: MdAgriculture },
      { to: '/weather', label: '기상 작전', detail: '예보 · 작업 캘린더', icon: MdCloud },
      { to: '/market', label: '시세 정보', detail: 'KAMIS 가격', icon: MdShowChart },
    ],
  },
  {
    label: 'Business',
    items: [
      { to: '/subsidy', label: '공익직불', detail: '자격 · 근거', icon: MdPayments },
      { to: '/documents', label: '행정 문서', detail: '신고 · 증빙', icon: MdArticle },
      { to: '/reviews', label: '판매 인사이트', detail: '리뷰 · 전략', icon: MdReviews },
    ],
  },
];

export default function Sidebar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  return (
    <aside className="sticky top-0 flex h-screen w-[268px] flex-col border-r border-gray-200 bg-white">
      <div className="border-b border-gray-200 px-4 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-primary text-white">
            <MdAutoAwesome className="text-2xl" />
          </div>
          <div className="min-w-0">
            <p className="text-base font-black tracking-tight text-gray-950">FarmOS</p>
            <p className="text-xs font-semibold text-primary">Agentic Farm Ops</p>
          </div>
        </div>
      </div>

      <div className="border-b border-gray-200 px-4 py-4">
        <NavLink
          to="/profile"
          className={({ isActive }) =>
            `flex items-center gap-3 rounded-lg border px-3 py-3 transition ${
              isActive
                ? 'border-primary/30 bg-primary/5 text-primary'
                : 'border-gray-200 bg-gray-50 text-gray-700 hover:border-primary/30 hover:bg-white'
            }`
          }
        >
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-white text-primary shadow-sm">
            <MdManageAccounts className="text-xl" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-bold">{user?.name ?? '농장 관리자'}</p>
            <p className="truncate text-xs text-gray-500">
              {user?.farmname || user?.main_crop || user?.user_id}
            </p>
          </div>
        </NavLink>
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="mb-5 last:mb-0">
            <p className="mb-2 px-2 text-[11px] font-black uppercase tracking-wide text-gray-400">
              {group.label}
            </p>
            <div className="space-y-1">
              {group.items.map(({ to, label, detail, icon: Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={to === '/'}
                  className={({ isActive }) =>
                    `group flex items-center gap-3 rounded-lg px-3 py-2.5 transition ${
                      isActive
                        ? 'bg-primary text-white shadow-sm'
                        : 'text-gray-700 hover:bg-gray-100 hover:text-gray-950'
                    }`
                  }
                >
                  {({ isActive }) => (
                    <>
                      <span
                        className={`flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-md ${
                          isActive ? 'bg-white/15 text-white' : 'bg-white text-primary shadow-sm'
                        }`}
                      >
                        <Icon className="text-xl" />
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-bold">{label}</span>
                        <span
                          className={`block truncate text-[11px] ${
                            isActive ? 'text-white/75' : 'text-gray-400'
                          }`}
                        >
                          {detail}
                        </span>
                      </span>
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div className="border-t border-gray-200 p-3">
        <button
          type="button"
          onClick={handleLogout}
          className="flex w-full items-center justify-center gap-2 rounded-lg px-3 py-2.5 text-sm font-bold text-gray-500 transition hover:bg-red-50 hover:text-red-600"
        >
          <MdLogout className="text-lg" />
          로그아웃
        </button>
        <div className="mt-2 flex items-center justify-center gap-1.5 text-[11px] font-semibold text-gray-400">
          <MdStorefront className="text-sm" />
          Harness Engineering
        </div>
      </div>
    </aside>
  );
}
