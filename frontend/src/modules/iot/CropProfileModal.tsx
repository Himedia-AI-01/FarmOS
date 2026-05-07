// 작물 프로필 설정 모달.
//
// 디자인 정책 (시스템 톤 일치):
//   - 배경 dim, 라운드 카드, 시스템 색상 변수(`--color-line/ink/primary/...`) 사용
//   - 입력 컨트롤: `field-label` + `input` / `select` (글로벌 css 클래스)
//   - 액션 버튼: `btn-primary` / `btn-outline`
//
// UX:
//   - 작물(12종 지원) + 생육 단계(5단계) 드롭다운
//   - 선택이 바뀌면 권장값 자동 적용 (RDA 표준재배지침 기반)
//   - 각 환경값(온도/습도/일조/N-P-K)은 자동 적용 후 사용자가 미세조정 가능
//   - "권장값 다시 적용" 버튼으로 수동 리셋 지원
import { useState, useEffect, useMemo } from 'react';
import { MdClose, MdAutoFixHigh, MdInfoOutline } from 'react-icons/md';
import type { CropProfile } from '@/types';
import {
  SUPPORTED_CROPS,
  GROWTH_STAGES,
  getCropStageProfile,
  isSupportedCrop,
  isGrowthStage,
  type SupportedCrop,
  type GrowthStage,
} from '@/constants/cropProfiles';

interface Props {
  open: boolean;
  onClose: () => void;
  current: CropProfile;
  onSave: (profile: CropProfile) => void;
}

export default function CropProfileModal({ open, onClose, current, onSave }: Props) {
  // current.name 이 지원 목록에 없으면 기본 토마토로 백오프(데이터 마이그레이션 시점 안전).
  const initialCrop: SupportedCrop = isSupportedCrop(current.name) ? current.name : '토마토';
  const initialStage: GrowthStage = isGrowthStage(current.growth_stage)
    ? current.growth_stage
    : '영양생장기';

  const [form, setForm] = useState<CropProfile>(current);

  useEffect(() => {
    setForm(current);
  }, [current, open]);

  // 권장값 미리보기 — UI 보조 표시 + "다시 적용" 동작에 사용
  const recommended = useMemo(() => {
    if (!isSupportedCrop(form.name) || !isGrowthStage(form.growth_stage)) return null;
    return getCropStageProfile(form.name, form.growth_stage);
  }, [form.name, form.growth_stage]);

  const isAtRecommended = !!recommended && (
    recommended.optimal_temp[0] === form.optimal_temp[0] &&
    recommended.optimal_temp[1] === form.optimal_temp[1] &&
    recommended.optimal_humidity[0] === form.optimal_humidity[0] &&
    recommended.optimal_humidity[1] === form.optimal_humidity[1] &&
    recommended.optimal_light_hours === form.optimal_light_hours &&
    recommended.nutrient_ratio.N === form.nutrient_ratio.N &&
    recommended.nutrient_ratio.P === form.nutrient_ratio.P &&
    recommended.nutrient_ratio.K === form.nutrient_ratio.K
  );

  if (!open) return null;

  const handleCropChange = (crop: SupportedCrop) => {
    // 작물 변경 시 권장값 자동 적용 (현재 stage 기준).
    const stage = isGrowthStage(form.growth_stage) ? form.growth_stage : initialStage;
    setForm(getCropStageProfile(crop, stage));
  };

  const handleStageChange = (stage: GrowthStage) => {
    // 단계 변경 시 권장값 자동 적용 (현재 crop 기준).
    const crop = isSupportedCrop(form.name) ? form.name : initialCrop;
    setForm(getCropStageProfile(crop, stage));
  };

  const reapplyRecommended = () => {
    if (!isSupportedCrop(form.name) || !isGrowthStage(form.growth_stage)) return;
    setForm(getCropStageProfile(form.name, form.growth_stage));
  };

  const handleSave = () => {
    onSave(form);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-lg max-h-[90vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-[color:var(--color-line-soft)]">
          <h3 className="text-lg font-bold text-[color:var(--color-ink)]">작물 프로필 설정</h3>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-[color:var(--color-ink-faint)] hover:text-[color:var(--color-ink-soft)] hover:bg-[color:var(--color-surface-deep)] transition-colors"
            aria-label="닫기"
          >
            <MdClose className="text-xl" />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 overflow-y-auto flex-1 space-y-4">
          {/* 안내 배너 */}
          <div className="flex items-start gap-2 rounded-xl border border-[color:var(--color-info)]/20 bg-[color:var(--tint-info)] px-3.5 py-2.5 text-[12.5px] leading-[1.55] text-[color:var(--color-info)]">
            <MdInfoOutline aria-hidden className="mt-0.5 flex-shrink-0 text-[16px]" />
            <p>
              작물 또는 생육 단계를 바꾸면 농촌진흥청(RDA) 표준재배지침 권장값이 자동 적용됩니다.
              필요 시 아래 입력란에서 직접 미세조정하실 수 있습니다.
            </p>
          </div>

          {/* 작물 + 생육 단계 (드롭다운) */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="field">
              <label htmlFor="crop-select" className="field-label">작물</label>
              <select
                id="crop-select"
                className="select"
                value={form.name}
                onChange={e => handleCropChange(e.target.value as SupportedCrop)}
              >
                {SUPPORTED_CROPS.map(c => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="stage-select" className="field-label">생육 단계</label>
              <select
                id="stage-select"
                className="select"
                value={form.growth_stage}
                onChange={e => handleStageChange(e.target.value as GrowthStage)}
              >
                {GROWTH_STAGES.map(s => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
          </div>

          {/* 권장값 적용 상태 + 다시 적용 버튼 */}
          {recommended && (
            <div className="flex items-center justify-between gap-2 rounded-xl bg-[color:var(--color-surface)] px-3.5 py-2 text-[12.5px]">
              <span className="flex items-center gap-1.5 text-[color:var(--color-ink-mute)]">
                <MdAutoFixHigh aria-hidden className="text-[15px] text-[color:var(--color-primary-dark)]" />
                {isAtRecommended
                  ? '현재 권장값이 적용되어 있습니다'
                  : '직접 수정한 값이 있습니다'}
              </span>
              <button
                type="button"
                onClick={reapplyRecommended}
                disabled={isAtRecommended}
                className="text-[12.5px] font-semibold text-[color:var(--color-primary-dark)] hover:text-[color:var(--color-primary)] disabled:text-[color:var(--color-ink-faint)] disabled:cursor-not-allowed"
              >
                권장값 다시 적용
              </button>
            </div>
          )}

          {/* 적정 온도 */}
          <div className="field">
            <label className="field-label">적정 온도 (°C)</label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                className="input num !w-24 text-center"
                value={form.optimal_temp[0]}
                onChange={e => setForm({ ...form, optimal_temp: [Number(e.target.value), form.optimal_temp[1]] })}
              />
              <span className="text-[color:var(--color-ink-faint)]">~</span>
              <input
                type="number"
                className="input num !w-24 text-center"
                value={form.optimal_temp[1]}
                onChange={e => setForm({ ...form, optimal_temp: [form.optimal_temp[0], Number(e.target.value)] })}
              />
              <span className="text-[12.5px] text-[color:var(--color-ink-faint)]">°C</span>
            </div>
          </div>

          {/* 적정 습도 */}
          <div className="field">
            <label className="field-label">적정 습도 (%)</label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={0}
                max={100}
                className="input num !w-24 text-center"
                value={form.optimal_humidity[0]}
                onChange={e => setForm({ ...form, optimal_humidity: [Number(e.target.value), form.optimal_humidity[1]] })}
              />
              <span className="text-[color:var(--color-ink-faint)]">~</span>
              <input
                type="number"
                min={0}
                max={100}
                className="input num !w-24 text-center"
                value={form.optimal_humidity[1]}
                onChange={e => setForm({ ...form, optimal_humidity: [form.optimal_humidity[0], Number(e.target.value)] })}
              />
              <span className="text-[12.5px] text-[color:var(--color-ink-faint)]">%</span>
            </div>
          </div>

          {/* 일조시간 */}
          <div className="field">
            <label className="field-label">적정 일조시간</label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={0}
                max={24}
                className="input num !w-24 text-center"
                value={form.optimal_light_hours}
                onChange={e => setForm({ ...form, optimal_light_hours: Number(e.target.value) })}
              />
              <span className="text-[12.5px] text-[color:var(--color-ink-faint)]">시간 / 일</span>
            </div>
          </div>

          {/* 양액 배합비 N-P-K */}
          <div className="field">
            <label className="field-label">양액 배합비 (N : P : K)</label>
            <div className="flex items-end gap-2">
              {(['N', 'P', 'K'] as const).map((key, idx) => (
                <div key={key} className="flex items-center gap-2">
                  <div className="flex flex-col items-center">
                    <span className="text-[11.5px] font-semibold text-[color:var(--color-ink-mute)]">{key}</span>
                    <input
                      type="number"
                      step="0.1"
                      min={0}
                      className="input num !w-20 text-center"
                      value={form.nutrient_ratio[key]}
                      onChange={e => setForm({
                        ...form,
                        nutrient_ratio: { ...form.nutrient_ratio, [key]: Number(e.target.value) },
                      })}
                    />
                  </div>
                  {idx < 2 && <span className="text-[color:var(--color-ink-faint)] pb-2">:</span>}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-[color:var(--color-line-soft)]">
          <button onClick={onClose} className="btn-outline !py-2 !text-[14px]">
            취소
          </button>
          <button onClick={handleSave} className="btn-primary !py-2 !text-[14px]">
            저장
          </button>
        </div>
      </div>
    </div>
  );
}
