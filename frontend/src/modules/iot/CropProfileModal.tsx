import { useState, useEffect } from 'react';
import { MdClose, MdAutoFixHigh } from 'react-icons/md';
import type { CropProfile } from '@/types';

const PRESETS: Record<string, CropProfile> = {
  토마토: { name: '토마토', growth_stage: '개화기', optimal_temp: [20, 28], optimal_humidity: [60, 80], optimal_light_hours: 14, nutrient_ratio: { N: 1.0, P: 1.2, K: 1.5 } },
  딸기: { name: '딸기', growth_stage: '착과기', optimal_temp: [15, 25], optimal_humidity: [60, 75], optimal_light_hours: 12, nutrient_ratio: { N: 0.8, P: 1.0, K: 1.5 } },
  상추: { name: '상추', growth_stage: '영양생장기', optimal_temp: [15, 22], optimal_humidity: [60, 70], optimal_light_hours: 12, nutrient_ratio: { N: 1.5, P: 0.8, K: 1.0 } },
  고추: { name: '고추', growth_stage: '개화기', optimal_temp: [22, 30], optimal_humidity: [60, 75], optimal_light_hours: 14, nutrient_ratio: { N: 1.2, P: 1.0, K: 1.3 } },
  오이: { name: '오이', growth_stage: '영양생장기', optimal_temp: [20, 28], optimal_humidity: [70, 85], optimal_light_hours: 13, nutrient_ratio: { N: 1.3, P: 1.0, K: 1.2 } },
};

const STAGES = ['육묘기', '영양생장기', '개화기', '착과기', '수확기'];

interface Props {
  open: boolean;
  onClose: () => void;
  current: CropProfile;
  onSave: (profile: CropProfile) => void;
}

export default function CropProfileModal({ open, onClose, current, onSave }: Props) {
  const [form, setForm] = useState<CropProfile>(current);

  useEffect(() => {
    setForm(current);
  }, [current, open]);

  if (!open) return null;

  const applyPreset = (name: string) => {
    const preset = PRESETS[name];
    if (preset) setForm({ ...preset });
  };

  const handleSave = () => {
    onSave(form);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b">
          <h3 className="text-lg font-bold text-[color:var(--color-ink)]">작물 프로필 설정</h3>
          <button onClick={onClose} className="p-1 hover:bg-[color:var(--color-surface-deep)] rounded-lg">
            <MdClose className="text-xl" />
          </button>
        </div>

        {/* Presets */}
        <div className="px-5 pt-4">
          <p className="text-sm text-[color:var(--color-ink-mute)] mb-2">프리셋</p>
          <div className="flex flex-wrap gap-2">
            {Object.keys(PRESETS).map(name => (
              <button
                key={name}
                onClick={() => applyPreset(name)}
                className={`px-3 py-1.5 rounded-full text-sm border transition-all ${
                  form.name === name
                    ? 'bg-green-600 text-white border-green-600'
                    : 'bg-white text-[color:var(--color-ink-soft)] border-[color:var(--color-line)] hover:border-green-400'
                }`}
              >
                <MdAutoFixHigh className="inline mr-1" />
                {name}
              </button>
            ))}
          </div>
        </div>

        {/* Form */}
        <div className="p-5 space-y-4">
          {/* 작물명 */}
          <div>
            <label className="block text-sm font-medium text-[color:var(--color-ink-soft)] mb-1">작물명</label>
            <input
              type="text"
              value={form.name}
              onChange={e => setForm({ ...form, name: e.target.value })}
              className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-green-500 focus:border-green-500"
            />
          </div>

          {/* 생육 단계 */}
          <div>
            <label className="block text-sm font-medium text-[color:var(--color-ink-soft)] mb-1">생육 단계</label>
            <select
              value={form.growth_stage}
              onChange={e => setForm({ ...form, growth_stage: e.target.value })}
              className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-green-500 focus:border-green-500"
            >
              {STAGES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>

          {/* 적정 온도 */}
          <div>
            <label className="block text-sm font-medium text-[color:var(--color-ink-soft)] mb-1">적정 온도 (C)</label>
            <div className="flex gap-2 items-center">
              <input
                type="number"
                value={form.optimal_temp[0]}
                onChange={e => setForm({ ...form, optimal_temp: [Number(e.target.value), form.optimal_temp[1]] })}
                className="w-24 px-3 py-2 border rounded-lg text-center"
              />
              <span className="text-[color:var(--color-ink-faint)]">~</span>
              <input
                type="number"
                value={form.optimal_temp[1]}
                onChange={e => setForm({ ...form, optimal_temp: [form.optimal_temp[0], Number(e.target.value)] })}
                className="w-24 px-3 py-2 border rounded-lg text-center"
              />
            </div>
          </div>

          {/* 적정 습도 */}
          <div>
            <label className="block text-sm font-medium text-[color:var(--color-ink-soft)] mb-1">적정 습도 (%)</label>
            <div className="flex gap-2 items-center">
              <input
                type="number"
                value={form.optimal_humidity[0]}
                onChange={e => setForm({ ...form, optimal_humidity: [Number(e.target.value), form.optimal_humidity[1]] })}
                className="w-24 px-3 py-2 border rounded-lg text-center"
              />
              <span className="text-[color:var(--color-ink-faint)]">~</span>
              <input
                type="number"
                value={form.optimal_humidity[1]}
                onChange={e => setForm({ ...form, optimal_humidity: [form.optimal_humidity[0], Number(e.target.value)] })}
                className="w-24 px-3 py-2 border rounded-lg text-center"
              />
            </div>
          </div>

          {/* 일조시간 */}
          <div>
            <label className="block text-sm font-medium text-[color:var(--color-ink-soft)] mb-1">적정 일조시간</label>
            <div className="flex items-center gap-2">
              <input
                type="number"
                value={form.optimal_light_hours}
                onChange={e => setForm({ ...form, optimal_light_hours: Number(e.target.value) })}
                className="w-24 px-3 py-2 border rounded-lg text-center"
                min={0}
                max={24}
              />
              <span className="text-[color:var(--color-ink-mute)] text-sm">시간</span>
            </div>
          </div>

          {/* 양액 배합 N-P-K */}
          <div>
            <label className="block text-sm font-medium text-[color:var(--color-ink-soft)] mb-1">양액 배합비 (N-P-K)</label>
            <div className="flex gap-2 items-center">
              <div className="text-center">
                <span className="text-xs text-[color:var(--color-ink-mute)]">N</span>
                <input
                  type="number"
                  step="0.1"
                  value={form.nutrient_ratio.N}
                  onChange={e => setForm({ ...form, nutrient_ratio: { ...form.nutrient_ratio, N: Number(e.target.value) } })}
                  className="w-20 px-2 py-2 border rounded-lg text-center"
                />
              </div>
              <span className="text-[color:var(--color-ink-faint)] mt-4">:</span>
              <div className="text-center">
                <span className="text-xs text-[color:var(--color-ink-mute)]">P</span>
                <input
                  type="number"
                  step="0.1"
                  value={form.nutrient_ratio.P}
                  onChange={e => setForm({ ...form, nutrient_ratio: { ...form.nutrient_ratio, P: Number(e.target.value) } })}
                  className="w-20 px-2 py-2 border rounded-lg text-center"
                />
              </div>
              <span className="text-[color:var(--color-ink-faint)] mt-4">:</span>
              <div className="text-center">
                <span className="text-xs text-[color:var(--color-ink-mute)]">K</span>
                <input
                  type="number"
                  step="0.1"
                  value={form.nutrient_ratio.K}
                  onChange={e => setForm({ ...form, nutrient_ratio: { ...form.nutrient_ratio, K: Number(e.target.value) } })}
                  className="w-20 px-2 py-2 border rounded-lg text-center"
                />
              </div>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 p-5 border-t">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-[color:var(--color-ink-mute)] hover:bg-[color:var(--color-surface-deep)] rounded-lg"
          >
            취소
          </button>
          <button
            onClick={handleSave}
            className="px-4 py-2 text-sm bg-green-600 text-white rounded-lg hover:bg-green-700"
          >
            저장
          </button>
        </div>
      </div>
    </div>
  );
}
