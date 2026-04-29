import { useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import Sidebar from './Sidebar';
import TopBar from './TopBar';
import MobileNav from './MobileNav';
import FarmAgentConsole from '@/components/agent/FarmAgentConsole';

const PAGE_TITLES: Record<string, string> = {
  '/': '운영 커맨드',
  '/diagnosis': '진단 워크벤치',
  '/diagnosis/chat': '진단 상담',
  '/iot': '시설 제어',
  '/reviews': '판매 인사이트',
  '/documents': '행정 문서',
  '/weather': '기상 작전',
  '/journal': '영농 기록',
  '/market': '시세 정보',
  '/subsidy': '공익직불',
  '/profile': '농장 프로필',
};

export default function AppLayout() {
  const { pathname } = useLocation();
  const [agentOpen, setAgentOpen] = useState(false);
  const title = PAGE_TITLES[pathname] || 'FarmOS';

  return (
    <div className="min-h-screen bg-surface text-gray-900">
      <div className="grid min-h-screen lg:grid-cols-[268px_minmax(0,1fr)] 2xl:grid-cols-[268px_minmax(0,1fr)_400px]">
        <div className="hidden lg:block">
          <Sidebar />
        </div>

        <div className="flex min-w-0 flex-col">
          <TopBar title={title} onOpenAgent={() => setAgentOpen(true)} />
          <main className="min-h-0 flex-1 overflow-y-auto px-3 py-4 pb-[88px] sm:px-5 lg:px-7 lg:py-6 lg:pb-6">
            <AnimatePresence mode="wait">
              <motion.div
                key={pathname}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.18, ease: 'easeOut' }}
                className="mx-auto w-full max-w-[1320px]"
              >
                <Outlet />
              </motion.div>
            </AnimatePresence>
          </main>
          <MobileNav onOpenAgent={() => setAgentOpen(true)} />
        </div>

        <aside className="hidden min-h-screen border-l border-gray-200 bg-white 2xl:block">
          <div className="sticky top-0 h-screen">
            <FarmAgentConsole />
          </div>
        </aside>
      </div>

      {agentOpen && (
        <div className="fixed inset-0 z-[70] flex justify-end bg-black/35 2xl:hidden">
          <button
            type="button"
            className="absolute inset-0 cursor-default"
            aria-label="Farm Agent 닫기"
            onClick={() => setAgentOpen(false)}
          />
          <div className="relative h-full w-full max-w-[420px]">
            <FarmAgentConsole surface="drawer" onClose={() => setAgentOpen(false)} />
          </div>
        </div>
      )}
    </div>
  );
}
