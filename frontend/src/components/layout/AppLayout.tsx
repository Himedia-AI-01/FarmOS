import { useEffect, useRef } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import Sidebar from './Sidebar';
import TopBar from './TopBar';
import MobileNav from './MobileNav';
import FarmAgentConsole from '@/components/agent/FarmAgentConsole';
import { useFarmAgentContext } from '@/context/FarmAgentContext';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { useScrollLock } from '@/hooks/useScrollLock';

const PAGE_TITLES: Record<string, string> = {
  '/': '커맨드',
  '/diagnosis': '진단 워크벤치',
  '/diagnosis/chat': '진단 상담',
  '/iot': '시설 제어',
  '/reviews': '판매 인사이트',
  '/weather': '기상',
  '/journal': '영농일지',
  '/market': '시세 정보',
  '/subsidy': '공익직불',
  '/profile': '농장 프로필',
};

export default function AppLayout() {
  const { pathname } = useLocation();
  const { agentOpen, openAgent, closeAgent } = useFarmAgentContext();
  const title = PAGE_TITLES[pathname] || 'FarmOS';

  const drawerRef = useFocusTrap<HTMLDivElement>(agentOpen);
  useScrollLock(agentOpen);
  const mainRef = useRef<HTMLElement>(null);

  // Reset main scroll on route change (browser default doesn't help for our
  // overflow-y-auto main; sub-pages otherwise inherit prior scroll position).
  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0, behavior: 'auto' });
  }, [pathname]);

  // ESC closes the agent drawer
  useEffect(() => {
    if (!agentOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeAgent();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [agentOpen, closeAgent]);

  return (
    <div className="min-h-screen bg-[color:var(--color-surface)] text-[color:var(--color-ink)]">
      <a href="#main-content" className="skip-link">
        본문 바로가기
      </a>
      <div className="grid min-h-screen lg:grid-cols-[260px_minmax(0,1fr)] 2xl:grid-cols-[260px_minmax(0,1fr)_420px]">
        <div className="hidden lg:block">
          <Sidebar />
        </div>

        <div className="flex min-w-0 flex-col">
          <TopBar title={title} onOpenAgent={openAgent} />
          <main
            ref={mainRef}
            id="main-content"
            tabIndex={-1}
            aria-label="본문"
            className="min-h-0 flex-1 overflow-y-auto px-4 pb-[100px] pt-5 sm:px-6 sm:pt-6 lg:px-10 lg:pb-10 lg:pt-8"
          >
            <AnimatePresence mode="wait">
              <motion.div
                key={pathname}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -3 }}
                transition={{ duration: 0.2, ease: [0.2, 0.7, 0.2, 1] }}
                className="mx-auto w-full max-w-[1240px]"
              >
                <Outlet />
              </motion.div>
            </AnimatePresence>
          </main>
          <MobileNav onOpenAgent={openAgent} />
        </div>

        <aside
          aria-label="Farm Agent"
          className="hidden min-h-screen border-l border-[color:var(--color-line)] bg-[color:var(--color-card)] 2xl:block"
        >
          <div className="sticky top-0 h-screen">
            <FarmAgentConsole />
          </div>
        </aside>
      </div>

      <AnimatePresence>
        {agentOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="fixed inset-0 z-[70] flex justify-end bg-[color:var(--color-ink)]/45 backdrop-blur-sm 2xl:hidden"
            role="presentation"
          >
            <button
              type="button"
              tabIndex={-1}
              aria-hidden
              className="absolute inset-0 cursor-default"
              onClick={closeAgent}
            />
            <motion.div
              ref={drawerRef}
              role="dialog"
              aria-modal="true"
              aria-label="Farm Agent"
              tabIndex={-1}
              initial={{ x: '100%' }}
              animate={{ x: 0 }}
              exit={{ x: '100%' }}
              transition={{ type: 'spring', stiffness: 320, damping: 34 }}
              className="relative h-full w-full max-w-[460px] bg-[color:var(--color-card)] shadow-[var(--shadow-lg)]"
            >
              <FarmAgentConsole surface="drawer" onClose={closeAgent} />
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
