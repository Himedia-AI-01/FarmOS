import { useEffect, useMemo, useRef, useState } from 'react';
import {
  MdAddPhotoAlternate,
  MdAutoAwesome,
  MdChatBubbleOutline,
  MdClose,
  MdHistory,
  MdOutlineArticle,
  MdRefresh,
  MdRestartAlt,
  MdSend,
  MdStopCircle,
} from 'react-icons/md';
import { FARMOS_BACKEND_ORIGIN } from '@/lib/api';
import { useFarmAgentContext } from '@/context/FarmAgentContext';
import type { FarmAgentMessage } from '@/hooks/useFarmAgent';
import { AgentMarkdown } from './AgentMarkdown';
import { ReasoningTrace } from './ReasoningTrace';
import { ActionApproval } from './ActionApproval';
import { MicButton } from './MicButton';
import { EmptyState, Spinner, StatusDot } from '@/components/ui';
import { cn } from '@/lib/cn';

interface FarmAgentConsoleProps {
  surface?: 'rail' | 'drawer';
  onClose?: () => void;
}

// Each preset maps to one or more existing farm-agent tools, so the orchestrator
// can answer without follow-up clarification. Keep prompts concrete (verbs the
// model can act on) rather than open questions.
const STARTER_PROMPTS = [
  // 오케스트레이터 종합 → 다중 도구 호출
  { label: '오늘의 작업 우선순위', prompt: '오늘 해야 할 작업을 우선순위로 정리해줘' },
  // get_recent_iot_decisions / get_current_weather
  { label: '관수 판단', prompt: '현재 센서 상태에서 관수가 필요한지 판단해줘' },
  // list_eligible_subsidies / check_eligibility_rule
  { label: '직불 자격', prompt: '내 농장 기준 공익직불 신청 리스크를 확인해줘' },
  // get_market_prices_for_crop + get_weather_risk_advisory
  { label: '출하 타이밍', prompt: '최근 시세와 날씨를 보고 출하 타이밍을 봐줘' },
  // get_weather_risk_advisory (작물 자동 매칭)
  { label: '주간 기상 위험', prompt: '이번 주 서리·폭염·호우·강풍 위험을 내 작물 기준으로 분석해줘' },
  // diagnose_pest 유도 (작물·지역은 프로필에서 자동)
  { label: '병해충 위험 점검', prompt: '내 작물에 지금 시기 자주 발생하는 병해충과 예방법을 알려줘' },
  // list_journal_entries + get_journal_daily_summary
  { label: '영농일지 요약', prompt: '최근 7일 영농일지를 요약하고 누락된 작업이 있는지 알려줘' },
  // get_recent_iot_decisions
  { label: 'IoT 제어 이력', prompt: '최근 24시간 자율 IoT 에이전트 판단을 요약해줘' },
  // search_subsidy_obligation_check (8대 준수사항 매트릭스)
  { label: '농약 안전사용', prompt: '농약 안전사용 의무사항과 위반 시 직불금 감액 영향을 알려줘' },
  // get_market_prices_for_crop
  { label: '내 작물 시세', prompt: '내 주작물 최근 KAMIS 시세 동향과 전일 대비 변화를 알려줘' },
];

function MessageBubble({
  message,
  onApprove,
  onReject,
}: {
  message: FarmAgentMessage;
  onApprove: (id: string, action: Record<string, unknown>) => void;
  onReject: (id: string) => void;
}) {
  const isUser = message.role === 'user';

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] space-y-2">
          {message.attachments?.map((att, idx) => (
            <img
              key={idx}
              src={att.url.startsWith('http') ? att.url : `${FARMOS_BACKEND_ORIGIN}${att.url}`}
              alt={att.alt || '첨부 이미지'}
              className="ml-auto block max-h-48 rounded-2xl border border-[color:var(--color-line)] object-cover"
            />
          ))}
          <div className="rounded-2xl rounded-tr-md bg-[color:var(--color-primary)] px-4 py-2.5 text-white">
            <p className="whitespace-pre-wrap text-[14.5px] leading-[1.55]">{message.content}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="flex max-w-[92%] gap-2.5">
        <span className="mt-1 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-xl bg-[color:var(--color-primary-soft)] text-[color:var(--color-primary-dark)]">
          <MdAutoAwesome className="text-[18px]" />
        </span>
        <div className="min-w-0 flex-1 space-y-2">
          <ReasoningTrace
            steps={message.steps}
            toolCalls={message.toolCalls}
            streaming={message.streaming}
          />
          <div
            className={`rounded-2xl rounded-tl-md border px-4 py-3.5 ${
              message.error
                ? 'border-[color:var(--color-danger-light)] bg-[color:var(--color-danger-light)]/30'
                : 'border-[color:var(--color-line)] bg-[color:var(--color-card)]'
            }`}
          >
            {message.content ? (
              <AgentMarkdown content={message.content} citations={message.citations} />
            ) : message.streaming ? (
              <div className="flex items-center gap-1.5 py-1" aria-live="polite">
                <span className="h-2 w-2 rounded-full bg-[color:var(--color-primary)]/70 animate-pulse" />
                <span className="h-2 w-2 rounded-full bg-[color:var(--color-primary)]/50 animate-pulse [animation-delay:120ms]" />
                <span className="h-2 w-2 rounded-full bg-[color:var(--color-primary)]/30 animate-pulse [animation-delay:240ms]" />
              </div>
            ) : (
              <p className="text-[14px] text-[color:var(--color-ink-mute)]">응답을 표시하지 못했습니다.</p>
            )}
            {message.streaming && message.content && (
              <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-[color:var(--color-primary)] align-middle" />
            )}
            {message.fastPath && (
              <span className="mt-3 inline-flex rounded-full bg-[color:var(--color-primary-soft)] px-2.5 py-0.5 text-[12px] font-semibold text-[color:var(--color-primary-dark)]">
                빠른 응답
              </span>
            )}
          </div>
          {message.lowConfidence && (
            <div
              role="note"
              aria-label="낮은 신뢰도 알림"
              className="mt-2 flex items-start gap-2 rounded-xl border border-[color:var(--color-accent)]/40 bg-[#FBF1D8] px-3.5 py-2.5 text-[13px] text-[#6B5413]"
            >
              <span aria-hidden className="mt-px text-[color:var(--color-accent-dark)]">⚠</span>
              <span className="leading-[1.6]">{message.lowConfidence.hint}</span>
            </div>
          )}
          {message.action && (
            <ActionApproval
              proposal={message.action}
              onApprove={(action) => onApprove(message.id, action)}
              onReject={() => onReject(message.id)}
            />
          )}
        </div>
      </div>
    </div>
  );
}

export default function FarmAgentConsole({
  surface = 'rail',
  onClose,
}: FarmAgentConsoleProps) {
  const {
    messages,
    sessionId,
    briefing,
    briefingLoading,
    busy,
    error,
    threads,
    send,
    sendVoice,
    sendImage,
    approveAction,
    rejectAction,
    stop,
    reset,
    fetchBriefing,
    loadThread,
  } = useFarmAgentContext();
  const [input, setInput] = useState('');
  const [showThreads, setShowThreads] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    void fetchBriefing(false);
  }, [fetchBriefing]);

  const lastTick = useMemo(() => {
    const last = messages[messages.length - 1];
    return `${messages.length}-${last?.content.length ?? 0}-${last?.toolCalls.length ?? 0}`;
  }, [messages]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [lastTick]);

  const sendInput = (raw: string) => {
    const next = raw.trim();
    if (!next || busy) return;
    setInput('');
    void send(next);
  };

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    sendInput(input);
  };

  const handleFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const file = files[0];
    if (!file.type.startsWith('image/')) return;
    void sendImage(file);
  };

  return (
    <section
      className={`relative flex h-full min-h-0 flex-col bg-[color:var(--color-card)] ${
        surface === 'drawer' ? 'rounded-l-2xl' : ''
      }`}
      aria-label="Farm Agent"
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        handleFiles(e.dataTransfer.files);
      }}
    >
      {dragOver && (
        <div className="pointer-events-none absolute inset-0 z-50 flex items-center justify-center rounded-l-2xl border-2 border-dashed border-[color:var(--color-primary)] bg-[color:var(--color-primary-soft)]/85 backdrop-blur-sm">
          <div className="rounded-xl bg-[color:var(--color-card)] px-5 py-3 text-[14px] font-bold text-[color:var(--color-primary-dark)] shadow-lg">
            여기에 사진을 놓아 진단 요청
          </div>
        </div>
      )}

      <header className="flex items-center gap-2 border-b border-[color:var(--color-line-soft)] px-5 py-3.5">
        <div aria-hidden className="flex h-11 w-11 items-center justify-center rounded-2xl bg-[color:var(--color-primary)] text-white shadow-[var(--shadow-xs)]">
          <MdAutoAwesome className="text-[22px]" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-[16px] font-bold tracking-[-0.012em] text-[color:var(--color-ink)]">Farm Agent</h2>
            {busy ? (
              <StatusDot tone="warning" size={8} label="분석 중" />
            ) : (
              <StatusDot tone="success" size={8} pulse label="대기" />
            )}
          </div>
          <p className="mt-0.5 truncate text-[12.5px] text-[color:var(--color-ink-mute)]">
            {sessionId ? `세션 ${sessionId.slice(0, 8)}` : '대기 중'} · <span className="num">{messages.length}</span>개 메시지
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowThreads((v) => !v)}
          aria-label="대화 기록"
          aria-expanded={showThreads}
          aria-haspopup="listbox"
          className={cn(
            'icon-btn',
            showThreads && 'bg-[color:var(--color-primary)] text-white hover:bg-[color:var(--color-primary-dark)] hover:text-white',
          )}
        >
          <MdHistory aria-hidden className="text-[20px]" />
        </button>
        <button
          type="button"
          onClick={() => void fetchBriefing(true)}
          disabled={briefingLoading}
          aria-label="브리핑 새로고침"
          className="icon-btn"
        >
          {briefingLoading ? <Spinner size={18} tone="mute" label="" /> : <MdRefresh aria-hidden className="text-[20px]" />}
        </button>
        {onClose && (
          <button type="button" onClick={onClose} aria-label="닫기" className="icon-btn">
            <MdClose aria-hidden className="text-[20px]" />
          </button>
        )}
      </header>

      {showThreads && (
        <div className="max-h-64 overflow-y-auto border-b border-[color:var(--color-line-soft)] bg-[color:var(--color-surface)] p-3">
          {threads.length === 0 ? (
            <EmptyState
              compact
              icon={<MdChatBubbleOutline className="text-[20px]" />}
              title="최근 대화가 없어요"
              description="아래에서 새 질문을 시작해 보세요."
            />
          ) : (
            <ul className="space-y-1">
              {threads.map((t) => (
                <li key={t.session_id}>
                  <button
                    type="button"
                    onClick={() => {
                      void loadThread(t.session_id);
                      setShowThreads(false);
                    }}
                    className={`flex w-full items-start gap-2.5 rounded-xl px-3 py-2.5 text-left transition hover:bg-[color:var(--color-card)] ${
                      sessionId === t.session_id ? 'bg-[color:var(--color-card)] ring-1 ring-[color:var(--color-primary)]/30' : ''
                    }`}
                  >
                    <MdChatBubbleOutline className="mt-0.5 flex-shrink-0 text-[17px] text-[color:var(--color-ink-mute)]" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13.5px] font-semibold text-[color:var(--color-ink)]">
                        {t.last_user_message}
                      </span>
                      <span className="mt-0.5 block text-[11.5px] text-[color:var(--color-ink-mute)]">
                        {t.message_count}개 메시지
                        {t.updated_at
                          ? ` · ${new Date(t.updated_at).toLocaleString('ko-KR', {
                              month: 'short',
                              day: 'numeric',
                              hour: '2-digit',
                              minute: '2-digit',
                            })}`
                          : ''}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {messages.length === 0 && briefing && (
        <div className="border-b border-[color:var(--color-line-soft)] px-5 py-4">
          <div className="mb-2.5 flex items-center justify-between">
            <div className="flex items-center gap-2 text-[14px] font-bold text-[color:var(--color-ink)]">
              <MdOutlineArticle aria-hidden className="text-[18px] text-[color:var(--color-primary)]" />
              오늘 브리핑
            </div>
            {briefing.cached && (
              <span className="chip text-[11px]" title="캐시된 응답">
                cached
              </span>
            )}
          </div>
          <div className="max-h-44 overflow-y-auto rounded-xl bg-[color:var(--color-surface)] p-4">
            <AgentMarkdown content={briefing.content} />
          </div>
        </div>
      )}

      <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
        {messages.length === 0 ? (
          <div>
            <p className="mb-3 px-1 text-[12.5px] font-semibold tracking-wide text-[color:var(--color-ink-faint)] uppercase">
              빠른 시작
            </p>
            <div className="grid grid-cols-2 gap-2.5">
              {STARTER_PROMPTS.map((sp) => (
                <button
                  key={sp.label}
                  type="button"
                  onClick={() => sendInput(sp.prompt)}
                  className="rounded-xl border border-[color:var(--color-line)] bg-[color:var(--color-card)] px-3.5 py-3 text-left transition hover:border-[color:var(--color-primary-light)] hover:bg-[color:var(--color-primary-soft)]/30"
                >
                  <p className="text-[13px] font-bold text-[color:var(--color-primary-dark)]">{sp.label}</p>
                  <p className="mt-1 line-clamp-2 text-[12.5px] text-[color:var(--color-ink-mute)] leading-[1.5]">{sp.prompt}</p>
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((message) => (
            <MessageBubble
              key={message.id}
              message={message}
              onApprove={approveAction}
              onReject={rejectAction}
            />
          ))
        )}
        {error && (
          <div className="rounded-xl border border-[color:var(--color-danger-light)] bg-[color:var(--color-danger-light)]/40 px-3.5 py-2.5 text-[13.5px] text-[color:var(--color-danger)]">
            {error}
          </div>
        )}
      </div>

      <form onSubmit={submit} className="border-t border-[color:var(--color-line-soft)] p-4">
        <div className="flex items-end gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              handleFiles(e.target.files);
              e.target.value = '';
            }}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={busy}
            aria-label="사진으로 진단"
            className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl border border-[color:var(--color-line)] bg-[color:var(--color-card)] text-[color:var(--color-ink-mute)] transition hover:border-[color:var(--color-primary)] hover:bg-[color:var(--color-primary-soft)] hover:text-[color:var(--color-primary-dark)] disabled:opacity-50"
          >
            <MdAddPhotoAlternate aria-hidden className="text-[20px]" />
          </button>
          <MicButton onRecorded={sendVoice} disabled={busy} />
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                sendInput(input);
              }
            }}
            placeholder="질문 입력"
            className="textarea max-h-32 flex-1 placeholder:text-xs"
            style={{ minHeight: '44px' }}
            rows={1}
            disabled={busy}
          />
          <button
            type="submit"
            disabled={!input.trim() || busy}
            aria-label="전송"
            className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-xl bg-[color:var(--color-primary)] text-white shadow-[var(--shadow-xs)] transition hover:bg-[color:var(--color-primary-dark)] disabled:cursor-not-allowed disabled:bg-[color:var(--color-ink-disabled)] disabled:shadow-none"
          >
            {busy ? <Spinner size={18} tone="inverse" label="" /> : <MdSend aria-hidden className="text-[20px]" />}
          </button>
        </div>
        <div className="mt-2.5 flex items-center justify-between">
          <button
            type="button"
            onClick={reset}
            disabled={busy && messages.length === 0}
            className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12.5px] font-semibold text-[color:var(--color-ink-mute)] transition hover:bg-[color:var(--color-surface)] hover:text-[color:var(--color-ink)] disabled:opacity-40"
          >
            <MdRestartAlt className="text-[16px]" />
            새 대화
          </button>
          {busy && (
            <button
              type="button"
              onClick={stop}
              className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12.5px] font-semibold text-[color:var(--color-danger)] transition hover:bg-[color:var(--color-danger-light)]/30"
            >
              <MdStopCircle className="text-[16px]" />
              중지
            </button>
          )}
        </div>
      </form>
    </section>
  );
}
