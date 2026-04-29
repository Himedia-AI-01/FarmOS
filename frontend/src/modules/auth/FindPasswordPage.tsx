import { useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import toast from 'react-hot-toast';

const API_BASE = 'http://localhost:8000/api/v1';

export default function FindPasswordPage() {
  const [userId, setUserId] = useState('');
  const [email, setEmail] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [serverMessage, setServerMessage] = useState<string>('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!userId || !email) {
      toast.error('아이디와 이메일을 입력해주세요.');
      return;
    }
    setLoading(true);
    try {
      // 보안 강화 후 응답 형식: {"message": "...이메일로 발송했습니다..."}
      // 서버는 일치/불일치 모두 200을 반환 (계정 열거 차단). 토큰은 응답에 포함되지 않으며
      // 일치하는 사용자에게만 이메일로 발송된다 (PASSWORD_RESET_EMAIL_ENABLED=true 일 때).
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

  const inputClass = 'w-full px-4 py-3 text-lg border border-gray-300 rounded-xl focus:ring-2 focus:ring-primary focus:border-primary outline-none transition';

  return (
    <div className="min-h-screen bg-surface flex items-center justify-center p-4">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-md"
      >
        <div className="text-center mb-6">
          <h1 className="text-2xl font-bold text-gray-900">비밀번호 찾기</h1>
          <p className="text-gray-500 mt-1">
            {submitted
              ? '입력하신 이메일을 확인해주세요'
              : '가입 시 등록한 이메일로 재설정 안내를 보내드립니다'}
          </p>
        </div>

        <div className="card">
          {!submitted ? (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-base font-medium text-gray-700 mb-2">아이디</label>
                <input type="text" value={userId} onChange={e => setUserId(e.target.value)} placeholder="아이디를 입력하세요" className={inputClass} />
              </div>
              <div>
                <label className="block text-base font-medium text-gray-700 mb-2">이메일</label>
                <input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="가입 시 등록한 이메일" className={inputClass} />
              </div>
              <button type="submit" disabled={loading} className="btn-primary w-full">
                {loading ? '요청 중...' : '재설정 안내 받기'}
              </button>
              <p className="text-xs text-gray-400 text-center pt-2">
                보안 정책: 가입된 정보가 일치할 때만 이메일이 발송됩니다.
              </p>
            </form>
          ) : (
            <div className="text-center py-4">
              <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-success/10 text-3xl mb-4">
                ✉️
              </div>
              <p className="text-base text-gray-700 mb-4 leading-relaxed">{serverMessage}</p>
              <Link to="/login" className="btn-primary w-full">로그인 화면으로</Link>
            </div>
          )}

          <div className="mt-4 text-center flex justify-center gap-4">
            <Link to="/find-id" className="text-gray-500 hover:text-primary transition text-base">아이디 찾기</Link>
            <span className="text-gray-300">|</span>
            <Link to="/login" className="text-gray-500 hover:text-primary transition text-base">로그인</Link>
          </div>
        </div>
      </motion.div>
    </div>
  );
}
