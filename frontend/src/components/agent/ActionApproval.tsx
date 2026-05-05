import { useEffect, useState } from 'react';
import {
  MdAir,
  MdCancel,
  MdCheckCircle,
  MdExpandLess,
  MdExpandMore,
  MdLightbulb,
  MdShield,
  MdSpeed,
  MdTimer,
  MdTune,
  MdWaterDrop,
} from 'react-icons/md';
import type { ActionProposal } from '@/hooks/useFarmAgent';

interface ControlMeta {
  label: string;
  icon: React.ElementType;
  defaultAction: Record<string, unknown>;
  // 시뮬레이션: 실제 명령이 적용됐을 때 어떤 효과가 예상되는지를 사람이 이해할
  // 수 있게 한 줄로 보여준다.
  simulate: (action: Record<string, unknown>) => SimulationLine[];
  // 인라인 파라미터 조정용 슬라이더 정의 (key, min, max, step, unit, label)
  params: ParamSpec[];
}

interface ParamSpec {
  key: string;
  label: string;
  min: number;
  max: number;
  step: number;
  unit: string;
  default: number;
}

interface SimulationLine {
  icon: React.ElementType;
  text: string;
}

const CONTROL_META: Record<string, ControlMeta> = {
  ventilation: {
    label: '환기 가동',
    icon: MdAir,
    defaultAction: { window_open_pct: 100, fan_speed: 1500, on: true },
    params: [
      { key: 'window_open_pct', label: '창문 개방', min: 0, max: 100, step: 10, unit: '%', default: 100 },
      { key: 'fan_speed', label: '팬 회전수', min: 0, max: 3000, step: 100, unit: 'rpm', default: 1500 },
    ],
    simulate: (a) => [
      { icon: MdTune, text: `창문 ${a.window_open_pct ?? 0}% 개방` },
      { icon: MdSpeed, text: `팬 ${a.fan_speed ?? 0} rpm` },
      { icon: MdTimer, text: '예상 30분 가동 (자동 종료)' },
    ],
  },
  irrigation: {
    label: '관수 시작',
    icon: MdWaterDrop,
    defaultAction: { valve_open: true, duration_min: 8, flow_lpm: 2.3 },
    params: [
      { key: 'duration_min', label: '관수 시간', min: 1, max: 60, step: 1, unit: '분', default: 8 },
      { key: 'flow_lpm', label: '유량', min: 0.5, max: 10, step: 0.1, unit: 'L/min', default: 2.3 },
    ],
    simulate: (a) => {
      const dur = Number(a.duration_min ?? 0);
      const lpm = Number(a.flow_lpm ?? 0);
      const total = +(dur * lpm).toFixed(1);
      return [
        { icon: MdWaterDrop, text: `유량 ${lpm} L/min · 총 ${total} L` },
        { icon: MdTimer, text: `${dur}분 후 자동 차단` },
      ];
    },
  },
  lighting: {
    label: '보광 등 켜기',
    icon: MdLightbulb,
    defaultAction: { on: true, brightness_pct: 60, duration_min: 60 },
    params: [
      { key: 'brightness_pct', label: '밝기', min: 0, max: 100, step: 5, unit: '%', default: 60 },
      { key: 'duration_min', label: '점등 시간', min: 5, max: 240, step: 5, unit: '분', default: 60 },
    ],
    simulate: (a) => [
      { icon: MdLightbulb, text: `밝기 ${a.brightness_pct ?? 0}%` },
      { icon: MdTimer, text: `${a.duration_min ?? 0}분간 점등` },
    ],
  },
  shading: {
    label: '차광 펼치기',
    icon: MdShield,
    defaultAction: { shade_pct: 50, insulation_pct: 0, on: true },
    params: [
      { key: 'shade_pct', label: '차광막', min: 0, max: 100, step: 10, unit: '%', default: 50 },
      { key: 'insulation_pct', label: '보온막', min: 0, max: 100, step: 10, unit: '%', default: 0 },
    ],
    simulate: (a) => [
      { icon: MdShield, text: `차광막 ${a.shade_pct ?? 0}% / 보온막 ${a.insulation_pct ?? 0}%` },
      { icon: MdTimer, text: '일몰 전 자동 회수' },
    ],
  },
};

export function ActionApproval({
  proposal,
  onApprove,
  onReject,
}: {
  proposal: ActionProposal;
  onApprove: (action: Record<string, unknown>) => void;
  onReject: () => void;
}) {
  const meta = CONTROL_META[proposal.controlType] ?? {
    label: proposal.controlType,
    icon: MdShield,
    defaultAction: {},
    params: [],
    simulate: () => [],
  };
  const Icon = meta.icon;

  // 인라인 파라미터 조정 — agent 가 제안한 default 를 사용자가 슬라이더로 보정 가능.
  // useState 의 lazy initializer 는 첫 마운트에서만 실행되므로, 같은 컴포넌트
  // 인스턴스가 controlType 이 다른 새 proposal 을 받으면 이전 슬라이더 값이
  // 그대로 남는다 (예: irrigation → ventilation 으로 메타가 바뀌었는데 valve_open
  // 같은 stale 키가 action 에 남아있는 상태). proposal.controlType 변경 시
  // 명시적으로 리셋.
  const [action, setAction] = useState<Record<string, unknown>>(() => ({ ...meta.defaultAction }));
  const [paramsOpen, setParamsOpen] = useState(false);
  useEffect(() => {
    setAction({ ...meta.defaultAction });
    // meta is keyed off proposal.controlType; depend on the type so we don't
    // reset on every render (meta.defaultAction is a fresh object literal each
    // call but its content is stable for a given controlType).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proposal.controlType]);

  if (proposal.status === 'approved') {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-bold text-emerald-800">
        <MdCheckCircle className="text-lg" />
        {meta.label} 실행 완료
      </div>
    );
  }

  if (proposal.status === 'rejected') {
    return (
      <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-500">
        제안을 거절했습니다.
      </div>
    );
  }

  if (proposal.status === 'failed') {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
        실행에 실패했습니다. {proposal.detail ? `(${proposal.detail})` : ''}
      </div>
    );
  }

  const isExecuting = proposal.status === 'executing';
  const simLines = meta.simulate(action);

  return (
    <div className="rounded-xl border-2 border-amber-300 bg-amber-50 p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-amber-100 text-amber-700">
          <Icon className="text-xl" />
        </span>
        <div>
          <p className="text-xs font-black uppercase tracking-wide text-amber-700">
            제어 제안 (승인 필요)
          </p>
          <p className="text-sm font-bold text-amber-950">{meta.label}</p>
        </div>
      </div>

      {proposal.summary && (
        <p className="mb-2 text-xs leading-relaxed text-amber-900/80">{proposal.summary}</p>
      )}

      {/* 시뮬레이션 미리보기 — 실제로 어떤 명령이 나갈지 줄단위로 표시 */}
      {simLines.length > 0 && (
        <div className="mb-3 space-y-1 rounded-lg border border-amber-200 bg-white/70 p-2.5">
          <p className="mb-1 text-[10px] font-bold uppercase tracking-wide text-amber-700">
            실행 시뮬레이션
          </p>
          {simLines.map((line, i) => {
            const LineIcon = line.icon;
            return (
              <div key={i} className="flex items-center gap-1.5 text-xs text-amber-900">
                <LineIcon className="flex-shrink-0 text-sm text-amber-700" />
                <span>{line.text}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* 파라미터 인라인 조정 */}
      {meta.params.length > 0 && (
        <div className="mb-3">
          <button
            type="button"
            onClick={() => setParamsOpen((v) => !v)}
            className="flex w-full items-center gap-1 rounded-lg border border-amber-200 bg-white px-2.5 py-1.5 text-xs font-bold text-amber-800 hover:bg-amber-100 transition"
            disabled={isExecuting}
          >
            <MdTune className="text-sm" />
            세부 조정
            <span className="ml-auto">{paramsOpen ? <MdExpandLess /> : <MdExpandMore />}</span>
          </button>
          {paramsOpen && (
            <div className="mt-2 space-y-2 rounded-lg border border-amber-200 bg-white/70 p-2.5">
              {meta.params.map((p) => {
                const value = Number(action[p.key] ?? p.default);
                return (
                  <label key={p.key} className="block text-xs">
                    <div className="mb-1 flex items-center justify-between text-amber-900">
                      <span className="font-semibold">{p.label}</span>
                      <span className="font-mono font-bold">
                        {value}
                        {p.unit}
                      </span>
                    </div>
                    <input
                      type="range"
                      min={p.min}
                      max={p.max}
                      step={p.step}
                      value={value}
                      onChange={(e) =>
                        setAction((prev) => ({ ...prev, [p.key]: Number(e.target.value) }))
                      }
                      className="w-full accent-amber-600"
                      disabled={isExecuting}
                    />
                  </label>
                );
              })}
            </div>
          )}
        </div>
      )}

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => onApprove(action)}
          disabled={isExecuting}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-amber-600 px-3 py-2 text-sm font-bold text-white transition hover:bg-amber-700 disabled:opacity-60"
        >
          <MdCheckCircle className="text-base" />
          {isExecuting ? '실행 중...' : '실행'}
        </button>
        <button
          type="button"
          onClick={onReject}
          disabled={isExecuting}
          className="flex items-center justify-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm font-bold text-amber-800 transition hover:bg-amber-100 disabled:opacity-60"
        >
          <MdCancel className="text-base" />
          취소
        </button>
      </div>
    </div>
  );
}
