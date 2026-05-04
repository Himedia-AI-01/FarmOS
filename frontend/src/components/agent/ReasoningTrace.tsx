import { useState } from 'react';
import {
  MdAutoFixHigh,
  MdChecklist,
  MdExpandLess,
  MdExpandMore,
  MdSwitchAccount,
} from 'react-icons/md';
import type { ReasoningStep, ToolCall } from '@/hooks/useFarmAgent';

const TOOL_LABELS: Record<string, string> = {
  get_my_farm_profile: '내 농장 프로필',
  get_current_weather: '날씨 조회',
  get_market_prices: '시세 조회',
  get_market_prices_for_crop: '작물별 시세',
  get_recent_iot_decisions: '자율 제어 이력',
  list_journal_entries: '영농일지',
  get_journal_daily_summary: '일지 요약',
  diagnose_pest: '병해충 진단',
  list_eligible_subsidies: '직불 자격 매칭',
  check_eligibility_rule: '자격 규정 확인',
  search_subsidy_regulations: '시행지침 검색',
  get_subsidy_details: '지원금 상세',
  classify_pest_image: '이미지 자동 분류',
  task: '서브에이전트 위임',
  write_todos: '계획 수립',
};

function toolLabel(name: string) {
  return TOOL_LABELS[name] || name.replace(/_/g, ' ');
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
      >
        <span
          className={`h-1.5 w-1.5 flex-shrink-0 rounded-full ${
            call.output ? 'bg-emerald-500' : 'bg-cyan-500 animate-pulse'
          }`}
        />
        <span className="font-bold text-gray-900">{toolLabel(call.name)}</span>
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
              <p className="text-[10px] font-bold uppercase tracking-wide text-gray-400">결과</p>
              <pre className="mt-0.5 max-h-40 overflow-y-auto whitespace-pre-wrap break-words font-mono text-[11px] text-gray-700">
                {call.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </li>
  );
}
