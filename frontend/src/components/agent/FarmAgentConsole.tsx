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
import type { FarmAgentMessage, LowConfidence } from '@/hooks/useFarmAgent';
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
// 다중 도구·다중 신호 합성을 보여주는 빠른 시작 칩. 단일 도구 wrapper 가 아니라
// 적어도 2-3 개 데이터 소스를 가로질러 추론하는 시나리오만 큐레이션.
const STARTER_PROMPTS = [
  { label: 'What-if 시나리오', prompt: '내일 강풍 7m/s 예보가 떨어지면 오늘 예정된 작업을 어떻게 재조정해야 할지, 작물 단계와 어제 IoT 제어 이력까지 고려해서 알려줘' },
  { label: '출하·수확 골든타임', prompt: '5일 기상 윈도우, 주작물 KAMIS 시세 추세, 영농일지에 기록된 작물 성숙도 신호를 종합해서 향후 3일 안에 출하·수확 최적 타이밍을 알려줘' },
  { label: '작물 전환 ROI 비교', prompt: '현재 주작물 수익과 후보 대안 작물(같은 면적·지역)의 시세·받을 수 있는 직불금 차이를 비교해서 작물 전환 ROI를 분석해줘' },
  { label: '이상치 탐지 리포트', prompt: '어제 IoT 제어 이력·영농일지·날씨를 최근 7일 평균과 비교해서 의미있게 다른 점만 골라 리포트해줘' },
  { label: '주간 기상 위험 + 작물별 영향', prompt: '5일치 기상 위험(서리·폭염·강풍·호우·곰팡이병 환경)을 내 작물 단계와 교차해서 작업 가능 시간대까지 알려줘' },
  { label: '방제 의사결정 보조', prompt: '내 작물 현재 가장 위험한 병해충을 진단하고, 추천 농약 안전성·8대 준수사항 부합 여부까지 함께 검토해줘' },
  { label: '8대 준수사항 자가 점검', prompt: '공익직불 8대 준수사항을 영농일지·IoT 이력으로 교차 확인해 미흡한 항목과 보완 방법을 우선순위로 알려줘' },
  { label: '시즌 회고', prompt: '최근 90일 영농일지·IoT·시세·기상을 종합해 이번 시즌 잘된 점·아쉬운 점·내년 개선점을 정리해줘' },
  { label: '신규 작물 도입 진단', prompt: '내 지역에서 토마토를 새로 도입하면 어떨지 5일 기상 적합도, 시세 추세, 받을 수 있는 직불금 변화, 시기별 전형 병해충을 종합해 진단해줘' },
];

// 낮은 신뢰도 답변에 대한 보정 안내 카드. 단순 hint 한 줄에서, 도메인별 후속 질문
// 칩까지 같이 보여주도록 확장. 사용자가 다음에 무엇을 물어보면 신뢰도가 올라갈지를
// 가시화 — 도메인 기반 follow-up 으로 ReasoningBank 트라젝토리도 풍성해진다.
function LowConfidenceCard({ low }: { low: LowConfidence }) {
  const { sendAndOpen } = useFarmAgentContext();
  const followUps =
    low.domain === 'subsidy'
      ? [
          { label: '청년농 대상인지 확인', q: '저는 청년농 (만 39세 이하·영농 3년 이내) 인지 직접 확인해주시고 해당되는 직불금을 다시 매칭해주세요.' },
          { label: '농업경영체 등록 여부', q: '농업경영체 등록 상태에 따라 자격이 달라지는 직불금을 알려주세요.' },
          { label: '시행지침 원문 인용', q: '바로 직전 답변의 핵심 주장을 시행지침 조항으로 다시 인용해 정리해주세요.' },
        ]
      : low.domain === 'diagnosis'
        ? [
            { label: '증상 사진으로 다시 진단', q: '잎 앞·뒷면, 줄기를 모두 보여주는 사진으로 다시 진단해주세요.' },
            { label: '인근 지역 확산 정보', q: '내 지역에서 비슷한 해충·병징이 최근에 보고된 적이 있는지 확인해주세요.' },
          ]
        : [
            { label: '내 농장 데이터로 재분석', q: '내 농장 프로필과 최근 IoT 데이터를 기반으로 답을 다시 정리해주세요.' },
            { label: '근거 자료 같이 보기', q: '방금 답변의 근거가 된 출처와 데이터를 함께 보여주세요.' },
          ];

  return (
    <div
      role="note"
      aria-label="낮은 신뢰도 알림"
      className="mt-2 rounded-xl border border-[color:var(--color-accent)]/40 bg-[#FBF1D8] p-3.5 text-[13px] text-[#6B5413]"
    >
      <div className="flex items-start gap-2">
        <span aria-hidden className="mt-px text-[color:var(--color-accent-dark)]">⚠</span>
        <div className="flex-1 space-y-1.5">
          <p className="font-bold leading-[1.6]">{low.reason || '신뢰도가 낮은 답변입니다.'}</p>
          <p className="leading-[1.6] opacity-90">{low.hint}</p>
        </div>
      </div>
      {followUps.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5 border-t border-amber-200/60 pt-2.5">
          <span className="text-[11px] font-bold uppercase tracking-wide text-[#8C6D1E]">
            보정 질문
          </span>
          {followUps.map((f) => (
            <button
              key={f.label}
              type="button"
              onClick={() => void sendAndOpen(f.q)}
              className="rounded-full border border-amber-300 bg-white/70 px-2.5 py-0.5 text-[11px] font-bold text-[#6B5413] hover:bg-white transition"
            >
              {f.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

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
            <LowConfidenceCard low={message.lowConfidence} />
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

  // 브리핑 자동 로드는 FarmAgentProvider 가 한 번만 트리거 — 중복 fetch 방지.

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
