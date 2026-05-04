import { MdAir, MdCancel, MdCheckCircle, MdLightbulb, MdShield, MdWaterDrop } from 'react-icons/md';
import type { ActionProposal } from '@/hooks/useFarmAgent';

const CONTROL_META: Record<
  string,
  { label: string; icon: React.ElementType; defaultAction: Record<string, unknown> }
> = {
  ventilation: {
    label: '환기 가동',
    icon: MdAir,
    defaultAction: { window_open_pct: 100, fan_speed: 1500, on: true },
  },
  irrigation: {
    label: '관수 시작',
    icon: MdWaterDrop,
    defaultAction: { valve_open: true },
  },
  lighting: {
    label: '보광 등 켜기',
    icon: MdLightbulb,
    defaultAction: { on: true, brightness_pct: 60 },
  },
  shading: {
    label: '차광 펼치기',
    icon: MdShield,
    defaultAction: { shade_pct: 50, insulation_pct: 0, on: true },
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
  };
  const Icon = meta.icon;

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
      <p className="mb-3 text-xs leading-relaxed text-amber-900/80">
        에이전트가 위 작업을 권장합니다. 직접 확인 후 실행하세요.
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => onApprove(meta.defaultAction)}
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
