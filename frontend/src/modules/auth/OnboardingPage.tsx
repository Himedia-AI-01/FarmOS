import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import toast from 'react-hot-toast';
import { useAuth } from '@/context/AuthContext';
import { CROP_OPTIONS, FARMLAND_TYPES, FARMER_TYPES, safeAreaConvert } from '@/constants/farming';

const API_BASE = 'http://localhost:8000/api/v1';
const STEP_LABELS = ['계정', '농장', '작물', '영농', '완료'];

/* ────────────── 타입 ────────────── */
interface AccountForm {
  user_id: string;
  name: string;
  email: string;
  password: string;
  password_confirm: string;
}

interface FarmForm {
  farmname: string;
  postcode: string;
  roadAddress: string;
  detailAddress: string;
  area: string;
}

interface CropForm {
  main_crop: string;
  crop_variety: string;
  farmland_type: string;
}

interface DetailForm {
  is_promotion_area: boolean;
  has_farm_registration: boolean;
  farmer_type: string;
  years_rural_residence: string;
  years_farming: string;
}

/* ────────────── 애니메이션 ────────────── */
const slideVariants = {
  enter: (dir: number) => ({ x: dir > 0 ? 80 : -80, opacity: 0 }),
  center: { x: 0, opacity: 1 },
  exit: (dir: number) => ({ x: dir > 0 ? -80 : 80, opacity: 0 }),
};

/* ────────────── 메인 컴포넌트 ────────────── */
// 모듈 스코프 상수 — 컴포넌트 매 렌더마다 재생성되지 않도록 hoist.
// 이전에 컴포넌트 내부 변수였을 때 useEffect deps 정적 분석에서 빠지는 문제도 함께 해소.
const DRAFT_KEY = 'farmos_onboarding_draft';

export default function OnboardingPage() {
  const navigate = useNavigate();
  const { user, refreshUser } = useAuth();

  const isAnimating = useRef(false);

  const [step, setStep] = useState(1);
  const [direction, setDirection] = useState(1);
  const [loading, setLoading] = useState(false);

  // Step 1
  const [account, setAccount] = useState<AccountForm>({
    user_id: '', name: '', email: '', password: '', password_confirm: '',
  });

  // Step 2
  const [farm, setFarm] = useState<FarmForm>({
    farmname: '', postcode: '', roadAddress: '', detailAddress: '', area: '',
  });

  // Step 3
  const [crop, setCrop] = useState<CropForm>({
    main_crop: '', crop_variety: '', farmland_type: '',
  });

  // Step 4
  const [detail, setDetail] = useState<DetailForm>({
    is_promotion_area: false,
    has_farm_registration: false,
    farmer_type: '일반',
    years_rural_residence: '',
    years_farming: '',
  });

  // 로그인 상태 확인 + localStorage 초안 복원
  // 의존성에 user 포함 — 초기 마운트 시 user=null인 상태에서 빈 deps로 1회만 실행되면
  // checkAuth()가 user를 채워도 초안이 절대 복원되지 않는 버그가 있었다.
  // user.user_id를 dep로 사용해 같은 사용자에 대한 재실행을 방지한다.
  useEffect(() => {
    if (user) {
      try {
        const saved = localStorage.getItem(DRAFT_KEY);
        if (saved) {
          const draft = JSON.parse(saved);
          if (draft.farm) setFarm(draft.farm);
          if (draft.crop) setCrop(draft.crop);
          if (draft.detail) setDetail(draft.detail);
          setStep(draft.step && draft.step >= 2 && draft.step <= 5 ? draft.step : 2);
          return;
        }
      } catch { /* 손상된 데이터 무시 */ }
      setStep(2);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.user_id]);

  // Steps 2-4 입력 데이터를 localStorage에 자동 저장 (계정 정보 제외)
  useEffect(() => {
    if (step >= 2) {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ farm, crop, detail, step }));
    }
  }, [farm, crop, detail, step]);

  // 입력 중 페이지 이탈 경고
  useEffect(() => {
    const hasData = farm.farmname || farm.area || farm.roadAddress ||
                    crop.main_crop || crop.farmland_type ||
                    detail.years_farming || detail.years_rural_residence;
    if (step >= 2 && step <= 4 && hasData) {
      const handler = (e: BeforeUnloadEvent) => {
        // Chrome/Edge는 e.preventDefault()만으로 prompt를 표시하지 않는다.
        // returnValue=''까지 설정해야 모던 브라우저에서 일관되게 경고가 노출된다.
        e.preventDefault();
        e.returnValue = '';
      };
      window.addEventListener('beforeunload', handler);
      return () => window.removeEventListener('beforeunload', handler);
    }
  }, [step, farm, crop, detail]);

  // Kakao 주소 검색 스크립트 로드 — 명시적 https + 로드 실패 가시화
  useEffect(() => {
    if (document.getElementById('daum-postcode-script')) return;
    const script = document.createElement('script');
    script.id = 'daum-postcode-script';
    // protocol-relative(`//`) 대신 명시적 https — file:// 등 비표준 환경에서도 안전.
    script.src = 'https://t1.kakaocdn.net/mapjsapi/bundle/postcode/prod/postcode.v2.js';
    script.async = true;
    script.onerror = () => {
      console.warn('[onboarding] Kakao postcode 스크립트 로드 실패 — 주소 검색 사용 불가');
    };
    document.head.appendChild(script);
  }, []);

  const clearDraft = () => localStorage.removeItem(DRAFT_KEY);

  // 소프트 검증: 모든 필드 비어있으면 안내 토스트 (진행은 허용)
  const softValidateStep2 = () => {
    if (!farm.farmname && !farm.roadAddress && !farm.area) {
      toast('농장 정보를 입력하지 않으셨습니다.\n나중에 프로필에서 수정할 수 있어요.', { icon: '💡' });
    }
    goNext();
  };
  const softValidateStep3 = () => {
    if (!crop.main_crop && !crop.farmland_type) {
      toast('재배 작물을 선택하지 않으셨습니다.\n나중에 프로필에서 수정할 수 있어요.', { icon: '💡' });
    }
    goNext();
  };

  const goNext = () => {
    if (isAnimating.current) return;
    isAnimating.current = true;
    setDirection(1);
    setStep(s => Math.min(s + 1, 5));
  };
  const goPrev = () => {
    if (isAnimating.current) return;
    isAnimating.current = true;
    setDirection(-1);
    setStep(s => Math.max(s - 1, 1));
  };

  /* ────── Step 1: 회원가입 ────── */
  const handleAccountSubmit = async () => {
    if (!account.user_id || !account.name || !account.email || !account.password) {
      toast.error('필수 항목을 모두 입력해주세요.');
      return;
    }
    if (account.user_id.length < 4) {
      toast.error('아이디는 4자 이상이어야 합니다.');
      return;
    }
    if (account.password.length < 4) {
      toast.error('비밀번호는 4자 이상이어야 합니다.');
      return;
    }
    if (account.password !== account.password_confirm) {
      toast.error('비밀번호가 일치하지 않습니다.');
      return;
    }

    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/auth/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          user_id: account.user_id,
          name: account.name,
          email: account.email,
          password: account.password,
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || '회원가입에 실패했습니다.');
      }
      await refreshUser();
      toast.success('계정이 생성되었습니다!');
      goNext();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '회원가입에 실패했습니다.');
    } finally {
      setLoading(false);
    }
  };

  /* ────── 건너뛰기 ────── */
  const handleSkip = async () => {
    setLoading(true);
    try {
      const location = farm.detailAddress
        ? `${farm.roadAddress} ${farm.detailAddress}`.trim()
        : farm.roadAddress;

      const body = {
        farmname: farm.farmname,
        location,
        area: farm.area ? parseFloat(farm.area) : 0,
        main_crop: crop.main_crop,
        crop_variety: crop.crop_variety,
        farmland_type: crop.farmland_type,
        is_promotion_area: detail.is_promotion_area,
        has_farm_registration: detail.has_farm_registration,
        farmer_type: detail.farmer_type,
        years_rural_residence: detail.years_rural_residence ? parseInt(detail.years_rural_residence) : 0,
        years_farming: detail.years_farming ? parseInt(detail.years_farming) : 0,
      };

      await fetch(`${API_BASE}/auth/onboarding`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(body),
      });

      clearDraft();
      await refreshUser();
      toast.success('나중에 프로필에서 정보를 완성할 수 있습니다.');
      navigate('/', { replace: true });
    } catch {
      // 저장 실패해도 이동
      clearDraft();
      await refreshUser();
      navigate('/', { replace: true });
    } finally {
      setLoading(false);
    }
  };

  /* ────── Step 5: 온보딩 완료 ────── */
  const handleComplete = async () => {
    setLoading(true);
    try {
      const location = farm.detailAddress
        ? `${farm.roadAddress} ${farm.detailAddress}`.trim()
        : farm.roadAddress;

      const body = {
        farmname: farm.farmname,
        location,
        area: farm.area ? parseFloat(farm.area) : 0,
        main_crop: crop.main_crop,
        crop_variety: crop.crop_variety,
        farmland_type: crop.farmland_type,
        is_promotion_area: detail.is_promotion_area,
        has_farm_registration: detail.has_farm_registration,
        farmer_type: detail.farmer_type,
        years_rural_residence: detail.years_rural_residence ? parseInt(detail.years_rural_residence) : 0,
        years_farming: detail.years_farming ? parseInt(detail.years_farming) : 0,
      };

      const res = await fetch(`${API_BASE}/auth/onboarding`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error('프로필 저장에 실패했습니다.');

      clearDraft();
      await refreshUser();
      toast.success('FarmOS에 오신 것을 환영합니다! 🌱');
      navigate('/', { replace: true });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '저장에 실패했습니다.');
    } finally {
      setLoading(false);
    }
  };

  const handleAddressSearch = () => {
    if (typeof (window as any).daum === 'undefined') {
      toast.error('주소 검색 서비스를 불러올 수 없습니다.');
      return;
    }
    new (window as any).daum.Postcode({
      oncomplete(data: any) {
        setFarm(f => ({ ...f, postcode: data.zonecode, roadAddress: data.roadAddress, detailAddress: '' }));
      },
    }).open();
  };

  const inputClass = 'input';
  const selectClass = 'select';

  /* ────── Step Indicator ────── */
  const StepIndicator = () => (
    <div className="flex items-center justify-center gap-1 mb-7">
      {STEP_LABELS.map((label, i) => {
        const stepNum = i + 1;
        const isActive = step === stepNum;
        const isDone = step > stepNum;
        return (
          <div key={label} className="flex items-center">
            <div className="flex flex-col items-center">
              <div
                className={`w-9 h-9 rounded-full flex items-center justify-center text-[13.5px] font-bold transition-all duration-300 ${isDone
                  ? 'bg-[color:var(--color-primary)] text-white'
                  : isActive
                    ? 'bg-[color:var(--color-primary)] text-white scale-110 shadow-[0_4px_14px_-6px_rgba(31,92,61,0.45)]'
                    : 'bg-[color:var(--color-surface-deep)] text-[color:var(--color-ink-mute)]'
                  }`}
              >
                {isDone ? '✓' : stepNum}
              </div>
              <span className={`text-[12.5px] mt-1.5 font-semibold ${isActive ? 'text-[color:var(--color-primary-dark)]' : 'text-[color:var(--color-ink-mute)]'}`}>
                {label}
              </span>
            </div>
            {i < STEP_LABELS.length - 1 && (
              <div className={`w-9 h-[2px] mx-1 mt-[-18px] rounded-full transition-colors duration-300 ${isDone ? 'bg-[color:var(--color-primary)]' : 'bg-[color:var(--color-line)]'}`} />
            )}
          </div>
        );
      })}
    </div>
  );

  /* ────── Step 1: 계정 ────── */
  const renderStep1 = () => (
    <div className="space-y-4">
      <div className="text-center mb-2">
        <h2 className="text-xl font-bold text-[color:var(--color-ink)]">계정 만들기</h2>
        <p className="text-[color:var(--color-ink-mute)] text-sm mt-1">FarmOS 시작을 위한 기본 정보를 입력하세요</p>
      </div>
      <div>
        <label className="block text-base font-medium text-[color:var(--color-ink-soft)] mb-1">아이디 <span className="text-danger">*</span></label>
        <input type="text" value={account.user_id} onChange={e => setAccount(f => ({ ...f, user_id: e.target.value }))} placeholder="4~10자" className={inputClass} />
      </div>
      <div>
        <label className="block text-base font-medium text-[color:var(--color-ink-soft)] mb-1">이름 <span className="text-danger">*</span></label>
        <input type="text" value={account.name} onChange={e => setAccount(f => ({ ...f, name: e.target.value }))} placeholder="이름" className={inputClass} />
      </div>
      <div>
        <label className="block text-base font-medium text-[color:var(--color-ink-soft)] mb-1">이메일 <span className="text-danger">*</span></label>
        <input type="email" value={account.email} onChange={e => setAccount(f => ({ ...f, email: e.target.value }))} placeholder="email@example.com" className={inputClass} />
      </div>
      <div>
        <label className="block text-base font-medium text-[color:var(--color-ink-soft)] mb-1">비밀번호 <span className="text-danger">*</span></label>
        <input type="password" value={account.password} onChange={e => setAccount(f => ({ ...f, password: e.target.value }))} placeholder="4자 이상" className={inputClass} />
      </div>
      <div>
        <label className="block text-base font-medium text-[color:var(--color-ink-soft)] mb-1">비밀번호 확인 <span className="text-danger">*</span></label>
        <input type="password" value={account.password_confirm} onChange={e => setAccount(f => ({ ...f, password_confirm: e.target.value }))} placeholder="비밀번호 재입력" className={inputClass} />
      </div>
      <button onClick={handleAccountSubmit} disabled={loading} className="btn-primary w-full">
        {loading ? '처리 중...' : '다음'}
      </button>
      <div className="text-center">
        <Link to="/login" className="text-[color:var(--color-ink-mute)] hover:text-primary transition text-sm">
          이미 계정이 있으신가요? <span className="text-primary font-semibold">로그인</span>
        </Link>
      </div>
    </div>
  );

  /* ────── Step 2: 농장 기본정보 ────── */
  const renderStep2 = () => (
    <div className="space-y-4">
      <div className="text-center mb-2">
        <h2 className="text-xl font-bold text-[color:var(--color-ink)]">농장 기본정보</h2>
        <p className="text-[color:var(--color-ink-mute)] text-sm mt-1">농장 위치와 면적을 입력하세요</p>
      </div>
      <div>
        <label className="block text-base font-medium text-[color:var(--color-ink-soft)] mb-1">농장 이름</label>
        <input type="text" value={farm.farmname} onChange={e => setFarm(f => ({ ...f, farmname: e.target.value }))} placeholder="예: 행복한 사과농장" className={inputClass} />
      </div>
      <div>
        <label className="block text-base font-medium text-[color:var(--color-ink-soft)] mb-1">주소</label>
        <div className="flex gap-2">
          <input type="text" value={farm.postcode} readOnly placeholder="우편번호" className={`${inputClass} bg-[color:var(--color-surface-deep)] cursor-not-allowed flex-1`} />
          <button type="button" onClick={handleAddressSearch} className="px-4 py-3 bg-primary text-white rounded-xl font-semibold whitespace-nowrap hover:opacity-90 transition">
            우편번호 찾기
          </button>
        </div>
        <input type="text" value={farm.roadAddress} readOnly placeholder="도로명주소" className={`${inputClass} bg-[color:var(--color-surface-deep)] cursor-not-allowed mt-2`} />
        <input type="text" value={farm.detailAddress} onChange={e => setFarm(f => ({ ...f, detailAddress: e.target.value }))} placeholder="상세주소 입력" className={`${inputClass} mt-2`} />
      </div>
      <div>
        <label className="block text-base font-medium text-[color:var(--color-ink-soft)] mb-1">경작 면적 (평)</label>
        <input type="number" step="0.1" min="0" value={farm.area} onChange={e => setFarm(f => ({ ...f, area: e.target.value }))} placeholder="예: 3300" className={inputClass} />
        {(() => { const c = safeAreaConvert(farm.area); return c && (
          <p className="text-sm text-[color:var(--color-ink-mute)] mt-1">약 {c.m2.toFixed(0)}m² ({c.ha.toFixed(2)}ha)</p>
        ); })()}
      </div>
      <div className="flex gap-3">
        {!user && <button onClick={goPrev} className="btn-outline flex-1">이전</button>}
        <button onClick={softValidateStep2} className="btn-primary flex-1">다음</button>
      </div>
    </div>
  );

  /* ────── Step 3: 재배 작물 ────── */
  const renderStep3 = () => (
    <div className="space-y-4">
      <div className="text-center mb-2">
        <h2 className="text-xl font-bold text-[color:var(--color-ink)]">재배 작물</h2>
        <p className="text-[color:var(--color-ink-mute)] text-sm mt-1">주요 재배 작물과 농지 유형을 선택하세요</p>
      </div>
      <div>
        <label className="block text-base font-medium text-[color:var(--color-ink-soft)] mb-1">주요 재배 작물</label>
        <select value={crop.main_crop} onChange={e => setCrop(f => ({ ...f, main_crop: e.target.value }))} className={selectClass}>
          <option value="">선택하세요</option>
          {CROP_OPTIONS.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
      </div>
      <div>
        <label className="block text-base font-medium text-[color:var(--color-ink-soft)] mb-1">품종</label>
        <input type="text" value={crop.crop_variety} onChange={e => setCrop(f => ({ ...f, crop_variety: e.target.value }))} placeholder="예: 홍로, 부사, 쌀눈" className={inputClass} />
      </div>
      <div>
        <label className="block text-base font-medium text-[color:var(--color-ink-soft)] mb-2">농지 유형</label>
        <div className="grid grid-cols-2 gap-3">
          {FARMLAND_TYPES.map(t => (
            <button
              key={t.value}
              type="button"
              onClick={() => setCrop(f => ({ ...f, farmland_type: t.value }))}
              className={`p-3 rounded-xl border-2 text-left transition-all ${crop.farmland_type === t.value
                ? 'border-primary bg-primary/5'
                : 'border-[color:var(--color-line)] hover:border-[color:var(--color-line)]'
                }`}
            >
              <div className="font-semibold text-sm">{t.label}</div>
              <div className="text-xs text-[color:var(--color-ink-mute)] mt-0.5">{t.desc}</div>
            </button>
          ))}
        </div>
      </div>
      <div className="flex gap-3">
        <button onClick={goPrev} className="btn-outline flex-1">이전</button>
        <button onClick={softValidateStep3} className="btn-primary flex-1">다음</button>
      </div>
    </div>
  );

  /* ────── Step 4: 영농 상세 ────── */
  const renderStep4 = () => (
    <div className="space-y-4">
      <div className="text-center mb-2">
        <h2 className="text-xl font-bold text-[color:var(--color-ink)]">영농 상세정보</h2>
        <p className="text-[color:var(--color-ink-mute)] text-sm mt-1">보조금 적격성 확인에 필요한 정보입니다</p>
      </div>
      <div>
        <label className="block text-base font-medium text-[color:var(--color-ink-soft)] mb-1">농업인 유형</label>
        <select value={detail.farmer_type} onChange={e => setDetail(f => ({ ...f, farmer_type: e.target.value }))} className={selectClass}>
          {FARMER_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-base font-medium text-[color:var(--color-ink-soft)] mb-1">농촌 거주 연수</label>
          <input type="number" min="0" value={detail.years_rural_residence} onChange={e => setDetail(f => ({ ...f, years_rural_residence: e.target.value }))} placeholder="년" className={inputClass} />
        </div>
        <div>
          <label className="block text-base font-medium text-[color:var(--color-ink-soft)] mb-1">영농 경력 연수</label>
          <input type="number" min="0" value={detail.years_farming} onChange={e => setDetail(f => ({ ...f, years_farming: e.target.value }))} placeholder="년" className={inputClass} />
        </div>
      </div>

      {/* 토글 옵션 */}
      <div className="space-y-4 bg-[color:var(--color-surface)] rounded-2xl p-5">
        <label className="flex items-center justify-between gap-4 cursor-pointer">
          <div className="min-w-0">
            <div className="text-[14.5px] font-bold text-[color:var(--color-ink)]">농업경영체 등록</div>
            <div className="mt-0.5 text-[13px] text-[color:var(--color-ink-mute)] leading-[1.5]">등록 여부 · 보조금 필수 요건</div>
          </div>
          <span className="toggle">
            <input
              type="checkbox"
              checked={detail.has_farm_registration}
              onChange={e => setDetail(f => ({ ...f, has_farm_registration: e.target.checked }))}
            />
            <span className="toggle-track" />
            <span className="toggle-thumb" />
          </span>
        </label>
        <div className="border-t border-[color:var(--color-line-soft)]" />
        <label className="flex items-center justify-between gap-4 cursor-pointer">
          <div className="min-w-0">
            <div className="text-[14.5px] font-bold text-[color:var(--color-ink)]">농업진흥지역</div>
            <div className="mt-0.5 text-[13px] text-[color:var(--color-ink-mute)] leading-[1.5]">진흥지역 소재 여부 · 직불금 단가 차등</div>
          </div>
          <span className="toggle">
            <input
              type="checkbox"
              checked={detail.is_promotion_area}
              onChange={e => setDetail(f => ({ ...f, is_promotion_area: e.target.checked }))}
            />
            <span className="toggle-track" />
            <span className="toggle-thumb" />
          </span>
        </label>
      </div>

      <div className="flex gap-3">
        <button onClick={goPrev} className="btn-outline flex-1">이전</button>
        <button onClick={() => { setDirection(1); setStep(5); }} className="btn-primary flex-1">완료</button>
      </div>
    </div>
  );

  /* ────── Step 5: 완료 요약 ────── */
  const renderStep5 = () => (
    <div className="space-y-5">
      <div className="text-center">
        <img
          src="/illustrations/onboarding-complete.png"
          alt=""
          aria-hidden
          className="mx-auto h-32 w-32 object-contain mb-2"
        />
        <h2 className="text-[1.375rem] font-bold tracking-[-0.02em] text-[color:var(--color-ink)]">등록 정보 확인</h2>
        <p className="mt-1.5 text-[14px] text-[color:var(--color-ink-mute)]">입력한 정보를 확인하고 시작하세요</p>
      </div>

      {/* 요약 카드 */}
      <div className="space-y-3">
        <SummaryCard
          title="농장 정보"
          items={[
            { label: '농장명', value: farm.farmname || '-' },
            { label: '주소', value: farm.roadAddress ? `${farm.roadAddress} ${farm.detailAddress}`.trim() : '-' },
            { label: '면적', value: (() => { const c = safeAreaConvert(farm.area); return c ? `${farm.area}평 (약 ${c.ha.toFixed(2)}ha)` : '-'; })() },
          ]}
        />
        <SummaryCard
          title="재배 정보"
          items={[
            { label: '주요 작물', value: crop.main_crop || '-' },
            { label: '품종', value: crop.crop_variety || '-' },
            { label: '농지 유형', value: crop.farmland_type || '-' },
          ]}
        />
        <SummaryCard
          title="영농 상세"
          items={[
            { label: '농업인 유형', value: detail.farmer_type },
            { label: '농촌 거주', value: detail.years_rural_residence ? `${detail.years_rural_residence}년` : '-' },
            { label: '영농 경력', value: detail.years_farming ? `${detail.years_farming}년` : '-' },
            { label: '농업경영체', value: detail.has_farm_registration ? '등록됨' : '미등록' },
            { label: '진흥지역', value: detail.is_promotion_area ? '해당' : '비해당' },
          ]}
        />
      </div>

      {/* 보조금 적격성 미리보기 */}
      <EligibilityPreview
        area={safeAreaConvert(farm.area)?.m2 ?? 0}
        farmlandType={crop.farmland_type}
        isPromotionArea={detail.is_promotion_area}
        hasFarmRegistration={detail.has_farm_registration}
        yearsResidence={parseInt(detail.years_rural_residence) || 0}
        yearsFarming={parseInt(detail.years_farming) || 0}
      />

      <div className="flex gap-3">
        <button onClick={goPrev} className="btn-outline flex-1">수정하기</button>
        <button onClick={handleComplete} disabled={loading} className="btn-accent flex-1">
          {loading ? '저장 중...' : 'FarmOS 시작하기'}
        </button>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-[color:var(--color-surface)] flex items-center justify-center p-4">
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: [0.2, 0.7, 0.2, 1] }}
        className="w-full max-w-lg"
      >
        <div className="text-center mb-6">
          <span className="inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-[color:var(--color-primary)] text-white mb-3">
            <svg viewBox="0 0 32 32" fill="none" className="h-7 w-7" aria-hidden>
              <path d="M5 27c0-11 7-19 22-22-1 13-7 22-19 22-1 0-3 0-3 0z" fill="currentColor" opacity="0.95"/>
              <path d="M7 25C13 18 19 13 26 9" stroke="white" strokeWidth="1.6" strokeLinecap="round" opacity="0.6"/>
            </svg>
          </span>
          <h1 className="text-[1.625rem] font-bold tracking-[-0.022em] text-[color:var(--color-ink)]">FarmOS</h1>
          <p className="mt-1 text-[14px] text-[color:var(--color-ink-mute)]">스마트 농업 관리 시스템</p>
        </div>

        <StepIndicator />

        <div className="rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-card)] p-6 overflow-hidden">
          <AnimatePresence mode="wait" custom={direction}>
            <motion.div
              key={step}
              custom={direction}
              variants={slideVariants}
              initial="enter"
              animate="center"
              exit="exit"
              transition={{ duration: 0.25, ease: 'easeInOut' }}
              onAnimationComplete={() => { isAnimating.current = false; }}
            >
              {step === 1 && renderStep1()}
              {step === 2 && renderStep2()}
              {step === 3 && renderStep3()}
              {step === 4 && renderStep4()}
              {step === 5 && renderStep5()}
            </motion.div>
          </AnimatePresence>
        </div>

        {step > 1 && step < 5 && (
          <div className="mt-4 bg-white border border-[color:var(--color-line)] rounded-xl p-4 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 min-w-0">
              <span className="text-lg flex-shrink-0">💡</span>
              <p className="text-sm text-[color:var(--color-ink)]">
                이 단계는 건너뛸 수 있습니다. <br /><span className="text-[color:var(--color-ink)]">나중에 프로필에서 수정할 수 있습니다.</span>
              </p>
            </div>
            <button
              onClick={handleSkip}
              disabled={loading}
              className="flex-shrink-0 px-4 py-2 text-sm font-semibold text-[color:var(--color-ink-mute)] hover:text-primary border border-[color:var(--color-line)] hover:border-primary rounded-lg transition-colors"
            >
              건너뛰기
            </button>
          </div>
        )}
      </motion.div>
    </div>
  );
}

/* ────────────── 서브 컴포넌트 ────────────── */

function SummaryCard({ title, items }: { title: string; items: { label: string; value: string }[] }) {
  return (
    <div className="bg-[color:var(--color-surface)] rounded-2xl p-4">
      <h3 className="text-[14px] font-bold text-[color:var(--color-ink)] mb-2.5">{title}</h3>
      <div className="space-y-1.5">
        {items.map(item => (
          <div key={item.label} className="flex justify-between gap-3 text-[13.5px]">
            <span className="text-[color:var(--color-ink-mute)]">{item.label}</span>
            <span className="font-semibold text-[color:var(--color-ink)] text-right">{item.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

interface EligibilityProps {
  area: number;           // m²
  farmlandType: string;
  isPromotionArea: boolean;
  hasFarmRegistration: boolean;
  yearsResidence: number;
  yearsFarming: number;
}

function EligibilityPreview({
  area, farmlandType, isPromotionArea, hasFarmRegistration,
  yearsResidence, yearsFarming,
}: EligibilityProps) {
  // TODO: 여기에 공익직불금 적격성 판단 로직을 구현하세요
  // 아래의 기본 구현을 참고하여 면적직불금/소농직불금 판정 로직을 작성해주세요
  const areaM2 = area;
  const meetsMinArea = areaM2 >= 1000;
  const isSmallFarmArea = areaM2 >= 1000 && areaM2 <= 5000;
  const meetsResidence = yearsResidence >= 3;
  const meetsFarming = yearsFarming >= 3;

  const areaPaymentEligible = meetsMinArea && hasFarmRegistration;
  const smallFarmEligible = isSmallFarmArea && meetsResidence && meetsFarming && hasFarmRegistration;

  // 면적직불금 예상액 (간이 계산)
  let estimatedAreaPayment = 0;
  if (areaPaymentEligible) {
    const ha = areaM2 / 10000;
    const isRice = farmlandType === '논';
    const ratePerHa = isRice
      ? (isPromotionArea ? 2150000 : 1970000)
      : (isPromotionArea ? 1500000 : 1360000);
    estimatedAreaPayment = Math.round(ha * ratePerHa);
  }

  const showSmallFarm = smallFarmEligible && estimatedAreaPayment < 1300000;

  if (!meetsMinArea && !areaM2) {
    return (
      <div className="bg-[color:var(--color-surface)] border border-[color:var(--color-line)] rounded-xl p-4 text-center text-sm text-[color:var(--color-ink-mute)]">
        면적을 입력하면 공익직불금 적격 여부를 미리 확인할 수 있습니다
      </div>
    );
  }

  return (
    <div className="bg-[color:var(--tint-info)] border border-[color:var(--color-info)]/30 rounded-xl p-4">
      <h3 className="font-semibold text-info mb-3 text-sm flex items-center gap-1">
        📋 공익직불금 적격성 미리보기
      </h3>
      <div className="space-y-2 text-sm">
        {/* 면적직불금 */}
        <div className={`flex items-start gap-2 ${areaPaymentEligible ? 'text-success' : 'text-[color:var(--color-ink-faint)]'}`}>
          <span className="mt-0.5">{areaPaymentEligible ? '✅' : '❌'}</span>
          <div>
            <div className="font-medium">면적직불금</div>
            {areaPaymentEligible ? (
              <div className="text-xs text-[color:var(--color-ink-mute)]">
                예상 약 {(estimatedAreaPayment / 10000).toFixed(0)}만원/년
                ({farmlandType} {isPromotionArea ? '진흥' : '비진흥'}, {(areaM2 / 10000).toFixed(2)}ha)
              </div>
            ) : (
              <div className="text-xs">
                {!meetsMinArea ? '농지 1,000m² 이상 필요' : '농업경영체 등록 필요'}
              </div>
            )}
          </div>
        </div>

        {/* 소농직불금 */}
        <div className={`flex items-start gap-2 ${showSmallFarm ? 'text-success' : isSmallFarmArea ? 'text-warning' : 'text-[color:var(--color-ink-faint)]'}`}>
          <span className="mt-0.5">{showSmallFarm ? '✅' : '⚠️'}</span>
          <div>
            <div className="font-medium">소농직불금 (130만원/년)</div>
            <div className="text-xs text-[color:var(--color-ink-mute)]">
              {!isSmallFarmArea
                ? `농지 1,000~5,000m² 범위 외 (현재 ${areaM2.toFixed(0)}m²)`
                : !hasFarmRegistration
                  ? '농업경영체 등록 필요'
                  : !meetsResidence
                    ? `농촌 거주 3년 이상 필요 (현재 ${yearsResidence}년)`
                    : !meetsFarming
                      ? `영농 경력 3년 이상 필요 (현재 ${yearsFarming}년)`
                      : showSmallFarm
                        ? '소농직불금이 면적직불금보다 유리합니다'
                        : '면적직불금이 더 유리합니다'}
            </div>
          </div>
        </div>
      </div>
      <p className="text-xs text-[color:var(--color-ink-faint)] mt-3 border-t border-[color:var(--color-info)]/20 pt-2">
        * 간이 판정이며, 정확한 결과는 가구소득 등 추가 정보가 필요합니다.
        상세 적격성 검사는 가입 후 보조금 메뉴에서 확인하세요.
      </p>
    </div>
  );
}
