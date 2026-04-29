import { useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import {
  MdAgriculture,
  MdArticle,
  MdAutoAwesome,
  MdBugReport,
  MdCloud,
  MdDashboard,
  MdLogout,
  MdMoreHoriz,
  MdPayments,
  MdReviews,
  MdSensors,
  MdShowChart,
} from 'react-icons/md';
import { useAuth } from '@/context/AuthContext';

interface MobileNavProps {
  onOpenAgent: () => void;
}

const MAIN_TABS = [
  { to: '/', icon: MdDashboard, label: '커맨드' },
  { to: '/iot', icon: MdSensors, label: '제어' },
  { to: '/diagnosis', icon: MdBugReport, label: '진단' },
  { to: '/journal', icon: MdAgriculture, label: '일지' },
];

const MORE_TABS = [
  { to: '/weather', icon: MdCloud, label: '기상' },
  { to: '/market', icon: MdShowChart, label: '시세' },
  { to: '/subsidy', icon: MdPayments, label: '직불' },
  { to: '/documents', icon: MdArticle, label: '문서' },
  { to: '/reviews', icon: MdReviews, label: '리뷰' },
];

export default function MobileNav({ onOpenAgent }: MobileNavProps) {
  const [showMore, setShowMore] = useState(false);
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    setShowMore(false);
    await logout();
    navigate('/login');
  };

  return (
    <>
      {showMore && (
        <div className="fixed inset-0 z-50 lg:hidden" onClick={() => setShowMore(false)}>
          <div className="absolute inset-0 bg-black/35" />
          <div
            className="absolute bottom-[72px] left-0 right-0 rounded-t-lg border-t border-gray-200 bg-white p-4 shadow-xl"
            onClick={(event) => event.stopPropagation()}
          >
            <NavLink
              to="/profile"
              onClick={() => setShowMore(false)}
              className="mb-3 flex items-center gap-3 border-b border-gray-100 pb-3"
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <MdAutoAwesome className="text-xl" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-bold text-gray-950">{user?.name}</p>
                <p className="truncate text-xs text-gray-500">
                  {user?.farmname || user?.main_crop || user?.user_id}
                </p>
              </div>
            </NavLink>

            <div className="grid grid-cols-5 gap-2">
              {MORE_TABS.map(({ to, icon: Icon, label }) => (
                <NavLink
                  key={to}
                  to={to}
                  onClick={() => setShowMore(false)}
                  className={({ isActive }) =>
                    `flex flex-col items-center gap-1 rounded-lg border px-2 py-3 text-xs font-bold transition ${
                      isActive
                        ? 'border-primary bg-primary text-white'
                        : 'border-gray-200 bg-gray-50 text-gray-600'
                    }`
                  }
                >
                  <Icon className="text-xl" />
                  {label}
                </NavLink>
              ))}
            </div>

            <button
              type="button"
              onClick={handleLogout}
              className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg border border-gray-200 px-3 py-2.5 text-sm font-bold text-gray-500"
            >
              <MdLogout className="text-lg" />
              로그아웃
            </button>
          </div>
        </div>
      )}

      <nav className="fixed bottom-0 left-0 right-0 z-50 grid h-[72px] grid-cols-6 border-t border-gray-200 bg-white lg:hidden">
        {MAIN_TABS.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `flex flex-col items-center justify-center gap-1 text-xs font-bold ${
                isActive ? 'text-primary' : 'text-gray-400'
              }`
            }
          >
            <Icon className="text-2xl" />
            {label}
          </NavLink>
        ))}
        <button
          type="button"
          onClick={onOpenAgent}
          className="flex flex-col items-center justify-center gap-1 text-xs font-black text-primary"
        >
          <MdAutoAwesome className="text-2xl" />
          Agent
        </button>
        <button
          type="button"
          onClick={() => setShowMore((value) => !value)}
          className={`flex flex-col items-center justify-center gap-1 text-xs font-bold ${
            showMore ? 'text-primary' : 'text-gray-400'
          }`}
        >
          <MdMoreHoriz className="text-2xl" />
          더보기
        </button>
      </nav>
    </>
  );
}
