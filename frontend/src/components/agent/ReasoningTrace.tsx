import { useState } from 'react';
import {
  MdAutoFixHigh,
  MdChecklist,
  MdExpandLess,
  MdExpandMore,
  MdSwitchAccount,
} from 'react-icons/md';
import type { ReasoningStep, ToolCall } from '@/hooks/useFarmAgent';
import { ToolOutputCard } from './ToolOutputCard';

interface ToolMeta {
  label: string;
  description: string;
}

const TOOL_META: Record<string, ToolMeta> = {
  get_my_farm_profile: { label: '내 농장 프로필', description: '등록된 작물·면적·지역 등 농장 기본 정보를 가져옵니다.' },
  get_current_weather: { label: '날씨 조회', description: '기상청 초단기실황·예보를 농장 좌표로 조회합니다.' },
  get_market_prices: { label: '시세 조회', description: '농산물 도매시장 평균 시세 데이터를 가져옵니다.' },
  get_market_prices_for_crop: { label: '작물별 시세', description: '특정 작물의 최근 시세 추이를 가져옵니다.' },
  get_recent_iot_decisions: { label: '자율 제어 이력', description: 'AI 자율 제어 결정과 적용된 명령을 조회합니다.' },
  list_journal_entries: { label: '영농일지', description: '최근 영농일지 기록을 조회합니다.' },
  get_journal_daily_summary: { label: '일지 요약', description: '특정 일자 영농 활동을 요약합니다.' },
  diagnose_pest: { label: '병해충 진단', description: '작물·해충·지역 기반 NCPMS 방제 지침을 조회합니다.' },
  list_eligible_subsidies: { label: '직불 자격 매칭', description: '농장 프로필로 신청 가능한 직불금 후보를 산출합니다.' },
  check_eligibility_rule: { label: '자격 규정 확인', description: '특정 직불금의 자격 요건 충족 여부를 검토합니다.' },
  search_subsidy_regulations: { label: '시행지침 검색', description: '시행지침 본문에서 관련 조항을 의미 검색합니다.' },
  get_subsidy_details: { label: '지원금 상세', description: '직불금 코드로 상세 정보를 조회합니다.' },
  classify_pest_image: { label: '이미지 자동 분류', description: '업로드된 이미지를 VLM 모델로 해충 분류합니다.' },
  task: { label: '서브에이전트 위임', description: '도메인 전문 에이전트에게 하위 작업을 위임합니다.' },
  write_todos: { label: '계획 수립', description: '작업 단계를 todo 리스트로 정리합니다.' },
};

function toolLabel(name: string) {
  return TOOL_META[name]?.label || name.replace(/_/g, ' ');
}

function toolDescription(name: string) {
  return TOOL_META[name]?.description || `백엔드 도구 ${name} 호출`;
}

const SUBAGENT_LABELS: Record<string, string> = {
  'diagnosis-agent': '진단 전문가',
  'subsidy-agent': '직불·정책 전문가',
  'farm-data-agent': '농장 데이터',
  'verifier-agent': '안전 검증',
};

export function ReasoningTrace({
  steps,
  toolCalls,
  streaming,
}: {
  steps: ReasoningStep[];
  toolCalls: ToolCall[];
  streaming?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const totalSignals = steps.length + toolCalls.length;
  if (totalSignals === 0) return null;

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50/70">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left transition hover:bg-gray-100"
      >
        <span className="flex items-center gap-2 text-xs font-bold text-gray-700">
          <MdAutoFixHigh className={`text-base text-primary ${streaming ? 'animate-pulse' : ''}`} />
          에이전트 사고 흐름
          <span className="rounded-md bg-white px-1.5 py-0.5 text-[10px] text-gray-500">
            {totalSignals}
          </span>
        </span>
        {open ? <MdExpandLess className="text-base" /> : <MdExpandMore className="text-base" />}
      </button>
      {open && (
        <ol className="space-y-1.5 border-t border-gray-200 px-3 py-2">
          {steps.map((step, idx) => {
            if (step.kind === 'plan') {
              return (
                <li key={`step-${idx}`} className="flex gap-2 text-xs text-gray-700">
                  <MdChecklist className="mt-0.5 flex-shrink-0 text-base text-primary" />
                  <div className="min-w-0">
                    <p className="font-bold text-gray-900">계획 수립</p>
                    <pre className="mt-1 whitespace-pre-wrap font-sans text-[11px] leading-relaxed text-gray-600">
                      {step.markdown}
                    </pre>
                  </div>
                </li>
              );
            }
            return (
              <li key={`step-${idx}`} className="flex items-center gap-2 text-xs text-gray-700">
                <MdSwitchAccount className="flex-shrink-0 text-base text-cyan-600" />
                <span>
                  <span className="font-bold text-gray-900">{SUBAGENT_LABELS[step.name] || step.name}</span>
                  <span className="text-gray-500"> 에 위임</span>
                </span>
              </li>
            );
          })}
          {toolCalls.map((tc, idx) => (
            <ToolCallRow key={`tc-${idx}-${tc.toolCallId || tc.name}`} call={tc} />
          ))}
        </ol>
      )}
    </div>
  );
}

function ToolCallRow({ call }: { call: ToolCall }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = Boolean(call.args || call.output);
  return (
    <li className="text-xs text-gray-700">
      <button
        type="button"
        onClick={() => hasDetails && setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 rounded-md px-1 py-0.5 text-left transition hover:bg-white"
        disabled={!hasDetails}
        title={toolDescription(call.name)}
      >
        <span
          className={`h-1.5 w-1.5 flex-shrink-0 rounded-full ${
            call.output ? 'bg-emerald-500' : 'bg-cyan-500 animate-pulse'
          }`}
        />
        <span className="font-bold text-gray-900">{toolLabel(call.name)}</span>
        <span className="text-[10px] text-gray-400 font-mono">{call.name}</span>
        {call.output && (
          <span className="text-[10px] text-gray-400">
            {call.output.length > 60 ? `${call.output.length}자 응답` : '응답'}
          </span>
        )}
        {hasDetails && (
          <span className="ml-auto text-gray-400">
            {expanded ? <MdExpandLess /> : <MdExpandMore />}
          </span>
        )}
      </button>
      {expanded && hasDetails && (
        <div className="mt-1 ml-3.5 space-y-1.5 rounded-md border border-gray-200 bg-white p-2">
          {call.args !== undefined && (
            <div>
              <p className="text-[10px] font-bold uppercase tracking-wide text-gray-400">입력</p>
              <pre className="mt-0.5 whitespace-pre-wrap break-all font-mono text-[11px] text-gray-700">
                {typeof call.args === 'string' ? call.args : JSON.stringify(call.args, null, 2)}
              </pre>
            </div>
          )}
          {call.output && (
            <div>
              <p className="mb-1 text-[10px] font-bold uppercase tracking-wide text-gray-400">결과</p>
              <ToolOutputCard call={call} />
            </div>
          )}
        </div>
      )}
    </li>
  );
}
