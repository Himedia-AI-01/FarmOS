import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import toast from 'react-hot-toast';
import { useAuth } from '@/context/AuthContext';
import { Spinner } from '@/components/ui';

const ALLOWED_SHOP_ORIGIN = import.meta.env.VITE_SHOP_URL ?? 'https://shop.farmos.biz';

function LeafMark({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" fill="none" className={className} aria-hidden>
      <path d="M5 27c0-11 7-19 22-22-1 13-7 22-19 22-1 0-3 0-3 0z" fill="currentColor" opacity="0.95" />
      <path d="M7 25C13 18 19 13 26 9" stroke="white" strokeWidth="1.6" strokeLinecap="round" opacity="0.6" />
    </svg>
  );
}

export default function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { login } = useAuth();
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const idRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    idRef.current?.focus();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!userId || !password) {
      toast.error('아이디와 비밀번호를 입력해주세요.');
      return;
    }
    setLoading(true);
    try {
      await login(userId, password);
      toast.success('환영합니다!');
      const redirectParam = searchParams.get('redirect');
      if (redirectParam) {
        try {
          const redirectUrl = new URL(redirectParam);
          const allowedUrl = new URL(ALLOWED_SHOP_ORIGIN);
          if (redirectUrl.origin === allowedUrl.origin) {
            window.location.href = redirectParam;
            return;
          }
        } catch {
          // 잘못된 URL이면 기본 이동
        }
      }
      navigate('/');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '로그인에 실패했습니다.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[color:var(--color-surface)] p-4">
      <svg
        viewBox="0 0 320 320"
        aria-hidden
        className="pointer-events-none absolute -right-24 -top-24 h-[420px] w-[420px] text-[color:var(--color-primary)] opacity-[0.06]"
      >
        <path d="M40 280C40 168 110 70 280 30c-12 152-90 250-220 250-8 0-20 0-20 0z" fill="currentColor" />
      </svg>
      <svg
        viewBox="0 0 320 320"
        aria-hidden
        className="pointer-events-none absolute -bottom-32 -left-24 h-[380px] w-[380px] text-[color:var(--color-accent)] opacity-[0.05]"
      >
        <path d="M40 280C40 168 110 70 280 30c-12 152-90 250-220 250-8 0-20 0-20 0z" fill="currentColor" />
      </svg>

      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: [0.2, 0.7, 0.2, 1] }}
        className="relative w-full max-w-md"
      >
        <div className="mb-8 text-center">
          <span aria-hidden className="mb-4 inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-[color:var(--color-primary)] text-white shadow-[var(--shadow-sm)]">
            <LeafMark className="h-8 w-8" />
          </span>
          <h1 className="display-1">FarmOS</h1>
          <p className="mt-2 text-[15px] text-[color:var(--color-ink-mute)]">스마트 농장 관리 시스템</p>
        </div>

        <div className="rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-card)] p-7 shadow-[var(--shadow-sm)]">
          <h2 className="mb-5 text-center text-[1.125rem] font-bold text-[color:var(--color-ink)]">로그인</h2>
          <form onSubmit={handleSubmit} className="space-y-4" noValidate>
            <div className="field">
              <label htmlFor="login-id" className="field-label">아이디</label>
              <input
                ref={idRef}
                id="login-id"
                type="text"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                placeholder="아이디를 입력하세요"
                autoComplete="username"
                className="input"
                required
              />
            </div>
            <div className="field">
              <label htmlFor="login-pw" className="field-label">비밀번호</label>
              <input
                id="login-pw"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="비밀번호를 입력하세요"
                autoComplete="current-password"
                className="input"
                required
              />
            </div>
            <button
              type="submit"
              disabled={loading}
              aria-busy={loading}
              className="btn-primary mt-2 w-full"
            >
              {loading ? (
                <>
                  <Spinner size={16} tone="inverse" label="" />
                  로그인 중...
                </>
              ) : (
                '로그인'
              )}
            </button>
          </form>

          <nav aria-label="계정 관리" className="mt-6 flex flex-wrap justify-center gap-x-4 gap-y-2 text-[13.5px]">
            <Link to="/find-id" className="text-[color:var(--color-ink-mute)] transition hover:text-[color:var(--color-primary-dark)]">
              아이디 찾기
            </Link>
            <span aria-hidden className="text-[color:var(--color-line)]">·</span>
            <Link to="/find-password" className="text-[color:var(--color-ink-mute)] transition hover:text-[color:var(--color-primary-dark)]">
              비밀번호 찾기
            </Link>
            <span aria-hidden className="text-[color:var(--color-line)]">·</span>
            <Link to="/signup" className="font-semibold text-[color:var(--color-primary-dark)] transition hover:text-[color:var(--color-primary)]">
              회원가입
            </Link>
          </nav>
        </div>

        <div className="mt-5 rounded-xl border border-dashed border-[color:var(--color-line)] bg-[color:var(--color-card)]/60 px-4 py-3 text-center">
          <p className="eyebrow">테스트 계정</p>
          <p className="mt-1 num text-[13px] text-[color:var(--color-ink-mute)]">
            farmer01 / farm1234 · parkpear / pear5678
          </p>
        </div>
      </motion.div>
    </main>
  );
}
