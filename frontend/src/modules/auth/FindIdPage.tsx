import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { MdCheckCircle } from 'react-icons/md';
import toast from 'react-hot-toast';
import { Spinner } from '@/components/ui';

const API_BASE = '/api/v1';

export default function FindIdPage() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [result, setResult] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name || !email) {
      toast.error('이름과 이메일을 입력해주세요.');
      return;
    }
    setLoading(true);
    setResult(null);
    try {
      const res = await fetch(`${API_BASE}/auth/find-id`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ name, email }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || '아이디를 찾을 수 없습니다.');
      }
      const data = await res.json();
      if (data.user_id_masked) {
        setResult(data.user_id_masked);
      } else {
        toast.error('일치하는 회원 정보를 찾을 수 없습니다. 입력 정보를 확인해주세요.');
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '아이디 찾기에 실패했습니다.');
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
            아이디 찾기
          </h1>
          <p className="mt-2 text-[14.5px] text-[color:var(--color-ink-mute)]">
            가입 시 등록한 이름과 이메일을 입력하세요
          </p>
        </div>

        <div className="rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-card)] p-7 shadow-[var(--shadow-sm)]">
          {!result ? (
            <form onSubmit={handleSubmit} className="space-y-4" noValidate>
              <div className="field">
                <label htmlFor="findid-name" className="field-label">이름</label>
                <input
                  ref={nameRef}
                  id="findid-name"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="이름을 입력하세요"
                  className="input"
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="findid-email" className="field-label">이메일</label>
                <input
                  id="findid-email"
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
                {loading ? (<><Spinner size={16} tone="inverse" label="" />조회 중...</>) : '아이디 찾기'}
              </button>
            </form>
          ) : (
            <div role="status" aria-live="polite" className="py-2 text-center">
              <span aria-hidden className="mb-4 inline-flex h-14 w-14 items-center justify-center rounded-full bg-[color:var(--color-primary-soft)] text-[color:var(--color-primary)]">
                <MdCheckCircle className="text-[34px]" />
              </span>
              <p className="text-[15px] text-[color:var(--color-ink-soft)]">회원님의 아이디는</p>
              <p className="mt-2 num text-[1.625rem] font-bold tracking-[-0.02em] text-[color:var(--color-primary-dark)]">{result}</p>
              <p className="mt-2 text-[12.5px] text-[color:var(--color-ink-mute)]">
                개인정보 보호를 위해 일부가 가려져 있습니다
              </p>
              <Link to="/login" className="btn-primary mt-6 w-full">로그인하러 가기</Link>
            </div>
          )}

          <nav aria-label="계정 관리" className="mt-5 flex justify-center gap-4 text-[13.5px]">
            <Link to="/find-password" className="text-[color:var(--color-ink-mute)] transition hover:text-[color:var(--color-primary-dark)]">
              비밀번호 찾기
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
