import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { MdMarkEmailRead } from 'react-icons/md';
import toast from 'react-hot-toast';
import { Spinner } from '@/components/ui';

const API_BASE = 'http://localhost:8000/api/v1';

export default function FindPasswordPage() {
  const [userId, setUserId] = useState('');
  const [email, setEmail] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [serverMessage, setServerMessage] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const idRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    idRef.current?.focus();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!userId || !email) {
      toast.error('아이디와 이메일을 입력해주세요.');
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/auth/find-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ user_id: userId, email }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || '비밀번호 재설정 요청에 실패했습니다.');
      }
      setServerMessage(data.message || '비밀번호 재설정 안내를 이메일로 발송했습니다.');
      setSubmitted(true);
      toast.success('재설정 안내를 발송했습니다. 이메일을 확인해주세요.');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '요청에 실패했습니다.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-[color:var(--color-surface)] p-4">
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: [0.2, 0.7, 0.2, 1] }}
        className="w-full max-w-md"
      >
        <div className="mb-7 text-center">
          <h1 className="text-[1.75rem] font-bold tracking-[-0.022em] text-[color:var(--color-ink)]">
            비밀번호 찾기
          </h1>
          <p className="mt-2 text-[14.5px] text-[color:var(--color-ink-mute)]">
            {submitted
              ? '입력하신 이메일을 확인해주세요'
              : '가입 시 등록한 이메일로 재설정 안내를 보내드립니다'}
          </p>
        </div>

        <div className="rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-card)] p-7 shadow-[var(--shadow-sm)]">
          {!submitted ? (
            <form onSubmit={handleSubmit} className="space-y-4" noValidate>
              <div className="field">
                <label htmlFor="findpw-id" className="field-label">아이디</label>
                <input
                  ref={idRef}
                  id="findpw-id"
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
                <label htmlFor="findpw-email" className="field-label">이메일</label>
                <input
                  id="findpw-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="가입 시 등록한 이메일"
                  autoComplete="email"
                  className="input"
                  required
                />
              </div>
              <button type="submit" disabled={loading} aria-busy={loading} className="btn-primary mt-2 w-full">
                {loading ? (<><Spinner size={16} tone="inverse" label="" />요청 중...</>) : '재설정 안내 받기'}
              </button>
              <p className="pt-1.5 text-center text-[12.5px] leading-relaxed text-[color:var(--color-ink-mute)]">
                보안 정책: 가입된 정보가 일치할 때만 이메일이 발송됩니다.
              </p>
            </form>
          ) : (
            <div role="status" aria-live="polite" className="py-2 text-center">
              <span aria-hidden className="mb-4 inline-flex h-14 w-14 items-center justify-center rounded-full bg-[color:var(--color-primary-soft)] text-[color:var(--color-primary)]">
                <MdMarkEmailRead className="text-[34px]" />
              </span>
              <p className="mb-5 text-[15px] leading-[1.65] text-[color:var(--color-ink-soft)]">{serverMessage}</p>
              <Link to="/login" className="btn-primary w-full">로그인 화면으로</Link>
            </div>
          )}

          <nav aria-label="계정 관리" className="mt-5 flex justify-center gap-4 text-[13.5px]">
            <Link to="/find-id" className="text-[color:var(--color-ink-mute)] transition hover:text-[color:var(--color-primary-dark)]">
              아이디 찾기
            </Link>
            <span aria-hidden className="text-[color:var(--color-line)]">·</span>
            <Link to="/login" className="text-[color:var(--color-ink-mute)] transition hover:text-[color:var(--color-primary-dark)]">
              로그인
            </Link>
          </nav>
        </div>
      </motion.div>
    </main>
  );
}
