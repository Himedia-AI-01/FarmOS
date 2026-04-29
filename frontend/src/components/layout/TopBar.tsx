import { useNavigate } from 'react-router-dom';
import { MdAutoAwesome, MdLogout, MdMenuOpen } from 'react-icons/md';
import { useAuth } from '@/context/AuthContext';

interface TopBarProps {
  title: string;
  onOpenAgent: () => void;
}

export default function TopBar({ title, onOpenAgent }: TopBarProps) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  return (
    <header className="sticky top-0 z-30 flex h-[68px] items-center justify-between border-b border-gray-200 bg-white/95 px-3 backdrop-blur sm:px-5 lg:px-7">
      <div className="min-w-0">
        <div className="flex items-center gap-2 lg:hidden">
          <MdMenuOpen className="text-xl text-primary" />
          <span className="text-xs font-black uppercase tracking-wide text-primary">FarmOS</span>
        </div>
        <h1 className="truncate text-xl font-black tracking-tight text-gray-950">{title}</h1>
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onOpenAgent}
          className="inline-flex h-10 items-center gap-2 rounded-lg border border-primary/25 bg-primary/5 px-3 text-sm font-bold text-primary transition hover:bg-primary hover:text-white 2xl:hidden"
        >
          <MdAutoAwesome className="text-lg" />
          <span className="hidden sm:inline">Agent</span>
        </button>

        {user && (
          <div className="hidden items-center gap-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 md:flex">
            <div className="text-right leading-tight">
              <p className="text-sm font-bold text-gray-900">{user.name}님</p>
              <p className="max-w-[180px] truncate text-xs text-gray-500">
                {user.farmname || user.main_crop || user.user_id}
              </p>
            </div>
            <button
              type="button"
              onClick={handleLogout}
              className="flex h-8 w-8 items-center justify-center rounded-md text-gray-500 transition hover:bg-white hover:text-red-600"
              aria-label="로그아웃"
            >
              <MdLogout className="text-lg" />
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
