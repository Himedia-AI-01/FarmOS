import { useEffect, useMemo, useRef, useState } from 'react';
import toast from 'react-hot-toast';
import {
  MdCheckCircle,
  MdWarning,
  MdBlock,
  MdChat,
  MdClose,
  MdSend,
  MdExpandMore,
  MdExpandLess,
  MdRefresh,
  MdSmartToy,
  MdPerson,
} from 'react-icons/md';
import {
  type ChatMessage,
  type Citation,
  type EligibilityResult,
  type MatchResponse,
  type PaymentCalculation,
  type SourceClause,
  askSubsidyStream,
  fetchClauseSnippets,
  fetchMatch,
} from './api';
import SubsidySkeleton from './SubsidySkeleton';

const STATUS_CONFIG = {
  eligible: {
    label: '신청 가능',
    icon: MdCheckCircle,
    cardClass: 'border-green-300 bg-[color:var(--color-primary-soft)]',
    badgeClass: 'bg-green-600 text-white',
    iconClass: 'text-[color:var(--color-primary)]',
  },
  needs_review: {
    label: '추가 확인 필요',
    icon: MdWarning,
    cardClass: 'border-amber-300 bg-[color:var(--tint-warning)]',
    badgeClass: 'bg-amber-500 text-white',
    iconClass: 'text-[color:var(--color-accent-dark)]',
  },
  ineligible: {
    label: '해당 없음',
    icon: MdBlock,
    cardClass: 'border-[color:var(--color-line)] bg-[color:var(--color-surface)]',
    badgeClass: 'bg-gray-400 text-white',
    iconClass: 'text-[color:var(--color-ink-faint)]',
  },
} as const;

function formatKrw(amount: number | null): string {
  if (amount == null) return '';
  if (amount >= 10_000_000) return `${(amount / 10_000_000).toFixed(2)}천만원`;
  if (amount >= 10_000) return `${(amount / 10_000).toLocaleString()}만원`;
  return `${amount.toLocaleString()}원`;
}

export default function SubsidyPage() {
  const [match, setMatch] = useState<MatchResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<EligibilityResult | null>(null);
  const [askOpen, setAskOpen] = useState(false);

  useEffect(() => {
    fetchMatch()
      .then(setMatch)
      .catch((err) => toast.error(`매칭 실패: ${err.message}`))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <SubsidySkeleton />;
  }

  if (!match) {
    return <div className="card text-center">데이터를 불러올 수 없습니다.</div>;
  }

  const allResults = [
    ...match.eligible,
    ...match.needs_review,
    ...match.ineligible,
  ];

  return (
    <div className="space-y-6">
      {/* 안내 헤더 */}
      <div className="card bg-[color:var(--color-primary-soft)] border-[color:var(--color-primary-soft)]">
        <div className="flex items-start gap-3">
          <div className="text-4xl">🌾</div>
          <div>
            <h2 className="text-lg font-bold text-[color:var(--color-ink)]">
              2026년도 기본형 공익직불사업
            </h2>
            <p className="text-sm text-[color:var(--color-ink-mute)] mt-1">
              귀하의 프로필 정보를 바탕으로 신청 가능한 지원금을 분석했습니다.
              각 카드를 클릭하면 상세 정보와 근거 조항을 확인할 수 있습니다.
            </p>
          </div>
        </div>
      </div>

      {/* 결과 요약 — 수평 KPI 스트립 */}
      <dl className="grid grid-cols-3 divide-x divide-[color:var(--color-line-soft)] overflow-hidden rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-card)]">
        <div className="px-4 py-3.5">
          <dt className="text-[12px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-faint)]">신청 가능</dt>
          <dd className="mt-1 inline-flex items-baseline gap-1.5">
            <span className="num text-[1.75rem] font-bold leading-none text-[color:var(--color-primary-dark)]">{match.eligible.length}</span>
            <span className="text-[12px] font-semibold text-[color:var(--color-ink-mute)]">건</span>
          </dd>
        </div>
        <div className="px-4 py-3.5">
          <dt className="text-[12px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-faint)]">추가 확인</dt>
          <dd className="mt-1 inline-flex items-baseline gap-1.5">
            <span className="num text-[1.75rem] font-bold leading-none text-[color:var(--color-accent-dark)]">{match.needs_review.length}</span>
            <span className="text-[12px] font-semibold text-[color:var(--color-ink-mute)]">건</span>
          </dd>
        </div>
        <div className="px-4 py-3.5">
          <dt className="text-[12px] font-semibold uppercase tracking-wide text-[color:var(--color-ink-faint)]">해당 없음</dt>
          <dd className="mt-1 inline-flex items-baseline gap-1.5">
            <span className="num text-[1.75rem] font-bold leading-none text-[color:var(--color-ink-mute)]">{match.ineligible.length}</span>
            <span className="text-[12px] font-semibold text-[color:var(--color-ink-mute)]">건</span>
          </dd>
        </div>
      </dl>

      {/* 지원금 카드 목록 */}
      <div className="space-y-3">
        {allResults.length === 0 ? (
          <div className="card text-center text-[color:var(--color-ink-mute)]">
            등록된 지원금 정보가 없습니다.
          </div>
        ) : (
          allResults.map((r) => (
            <SubsidyCard key={r.subsidy_code} result={r} onClick={() => setSelected(r)} />
          ))
        )}
      </div>

      {/* 질의응답 버튼 (하단 고정) */}
      <button
        onClick={() => setAskOpen(true)}
        className="fixed bottom-24 lg:bottom-8 right-6 bg-primary text-white rounded-full px-5 py-3 shadow-lg flex items-center gap-2 hover:bg-primary/90 transition"
      >
        <MdChat className="text-xl" />
        <span className="font-semibold">시행지침 챗봇</span>
      </button>

      {/* 상세 드로어 */}
      {selected && (
        <DetailDrawer result={selected} onClose={() => setSelected(null)} />
      )}

      {/* Q&A 모달 */}
      {askOpen && <AskModal onClose={() => setAskOpen(false)} />}
    </div>
  );
}

// ─── 하위 컴포넌트 ────────────────────────────────────────

function SummaryTile({
  count,
  label,
  tone,
}: {
  count: number;
  label: string;
  tone: 'green' | 'amber' | 'gray';
}) {
  const toneMap = {
    green: 'bg-[color:var(--color-primary-soft)] border-[color:var(--color-primary-soft)] text-[color:var(--color-primary-dark)]',
    amber: 'bg-[color:var(--tint-warning)] border-amber-200 text-[color:var(--color-accent-dark)]',
    gray: 'bg-[color:var(--color-surface)] border-[color:var(--color-line)] text-[color:var(--color-ink-soft)]',
  };
  return (
    <div className={`border-2 rounded-2xl p-4 text-center ${toneMap[tone]}`}>
      <div className="text-3xl font-bold">{count}</div>
      <div className="text-xs font-semibold mt-1">{label}</div>
    </div>
  );
}

function SubsidyCard({
  result,
  onClick,
}: {
  result: EligibilityResult;
  onClick: () => void;
}) {
  const cfg = STATUS_CONFIG[result.status];
  const Icon = cfg.icon;

  return (
    <button
      onClick={onClick}
      className={`w-full text-left card border-2 transition-all hover:shadow-md ${cfg.cardClass}`}
    >
      <div className="flex items-start gap-3">
        <Icon className={`text-3xl ${cfg.iconClass} flex-shrink-0`} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="font-bold text-lg text-[color:var(--color-ink)]">
              {result.subsidy_name}
            </h3>
            <span
              className={`px-2 py-0.5 rounded-full text-xs font-semibold ${cfg.badgeClass}`}
            >
              {cfg.label}
            </span>
          </div>
          {result.estimated_amount_krw != null && (
            <p className="text-primary font-bold mt-1">
              예상 수령액: {formatKrw(result.estimated_amount_krw)}
            </p>
          )}
          {result.reasons.length > 0 && (
            <p className="text-sm text-[color:var(--color-ink-soft)] mt-2 line-clamp-2">
              {result.reasons[0].text}
            </p>
          )}
        </div>
      </div>
    </button>
  );
}

function DetailDrawer({
  result,
  onClose,
}: {
  result: EligibilityResult;
  onClose: () => void;
}) {
  // /match 는 snippet 을 비워 보내므로 drawer 가 열릴 때 RAG 로 lazy fetch.
  // process-level lru_cache 덕에 같은 subsidy 두 번째 열기는 거의 즉시.
  const [clauses, setClauses] = useState<SourceClause[]>(result.source_clauses);
  const [snippetsLoading, setSnippetsLoading] = useState(false);

  useEffect(() => {
    setClauses(result.source_clauses);
    const needsFetch = result.source_clauses
      .filter((c) => !c.snippet)
      .map((c) => ({ tag: c.tag, text: c.text }));
    if (needsFetch.length === 0) return;
    let cancelled = false;
    setSnippetsLoading(true);
    fetchClauseSnippets(needsFetch)
      .then(({ snippets }) => {
        if (cancelled) return;
        setClauses((prev) =>
          prev.map((c) => (c.snippet ? c : { ...c, snippet: snippets[c.tag] ?? null })),
        );
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : '조항 원문을 불러올 수 없습니다.';
        // 토스트 대신 콘솔로만 — 누군가 카드를 닫고 떠나면 토스트가 남아 거슬린다.
        console.warn('clause snippets fetch failed:', msg);
      })
      .finally(() => {
        if (!cancelled) setSnippetsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [result.subsidy_code, result.source_clauses]);

  return (
    <div
      className="fixed inset-0 z-40 flex items-end sm:items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-t-3xl sm:rounded-2xl w-full sm:max-w-lg max-h-[85vh] overflow-y-auto p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-3">
          <h2 className="text-xl font-bold text-[color:var(--color-ink)]">
            {result.subsidy_name}
          </h2>
          <button
            onClick={onClose}
            className="text-[color:var(--color-ink-faint)] hover:text-[color:var(--color-ink-soft)] p-1"
          >
            <MdClose className="text-2xl" />
          </button>
        </div>

        <span
          className={`inline-block px-3 py-1 rounded-full text-sm font-semibold mb-4 ${STATUS_CONFIG[result.status].badgeClass}`}
        >
          {STATUS_CONFIG[result.status].label}
        </span>

        {result.estimated_amount_krw != null && (
          <div className="bg-primary/5 border border-primary/20 rounded-xl p-4 mb-4">
            <p className="text-sm text-[color:var(--color-ink-mute)]">예상 수령액</p>
            <p className="text-2xl font-bold text-primary">
              {formatKrw(result.estimated_amount_krw)}
            </p>
            {result.payment_calculation && (
              <PaymentBreakdown calc={result.payment_calculation} />
            )}
          </div>
        )}

        <div className="space-y-3">
          <h3 className="font-semibold text-[color:var(--color-ink)]">판정 사유</h3>
          <ul className="space-y-2 text-sm text-[color:var(--color-ink-soft)]">
            {result.reasons.map((reason, i) => (
              <li key={i} className="flex gap-2 items-start">
                <span className="text-[color:var(--color-ink-faint)] flex-shrink-0 leading-relaxed">•</span>
                <span className="flex-1 leading-relaxed">
                  {reason.text}
                  {reason.source && (
                    <span className="ml-2 inline-flex items-center text-[11px] font-bold px-2 py-0.5 rounded-md bg-primary text-white shadow-sm align-middle whitespace-nowrap">
                      {reason.source}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>

        {(clauses.length > 0 || result.source_articles.length > 0) && (
          <div className="mt-4 pt-4 border-t border-[color:var(--color-line-soft)]">
            <div className="flex items-center justify-between mb-2">
              <p className="text-xs font-semibold text-[color:var(--color-ink-mute)]">출처</p>
              {snippetsLoading && (
                <span className="text-[11px] text-[color:var(--color-ink-faint)]">시행지침 원문 불러오는 중…</span>
              )}
            </div>
            {result.source_articles.length > 0 && (
              <p className="text-sm text-[color:var(--color-ink-soft)] mb-3">
                {result.source_articles.join(' · ')}
              </p>
            )}
            {clauses.length > 0 && (
              <ul className="space-y-2">
                {clauses.map((clause) => (
                  <SourceClauseCard
                    key={clause.tag}
                    clause={clause}
                    loading={snippetsLoading && !clause.snippet}
                  />
                ))}
              </ul>
            )}
          </div>
        )}

        <div className="mt-6 text-xs text-[color:var(--color-ink-faint)] text-center">
          실제 지급은 농업경영체 등록·현장 확인 등 정식 절차를 거쳐 확정됩니다.
        </div>
      </div>
    </div>
  );
}

// 예상 수령액의 계산식 표시 (fixed 는 1줄, tiered 는 구간별 여러 줄).
// 총액은 이미 상단에 크게 표시되므로 여기서는 내역과 합만 작게 보여 준다.
function PaymentBreakdown({ calc }: { calc: PaymentCalculation }) {
  return (
    <div className="mt-3 pt-3 border-t border-primary/20 space-y-2">
      <p className="text-[11px] font-semibold text-[color:var(--color-ink-mute)] uppercase tracking-wide">
        계산 내역
      </p>
      <ul className="space-y-1.5">
        {calc.steps.map((step, i) => (
          <li key={i} className="flex items-start justify-between gap-3 text-xs">
            <span className="text-[color:var(--color-ink-soft)] leading-relaxed flex-1">
              {step.description}
            </span>
            <span className="text-[color:var(--color-ink)] font-semibold whitespace-nowrap">
              {formatKrw(step.amount_krw)}
            </span>
          </li>
        ))}
      </ul>
      {calc.steps.length > 1 && (
        <div className="flex items-center justify-between pt-1.5 border-t border-[color:var(--color-line)] text-xs">
          <span className="font-semibold text-[color:var(--color-ink-soft)]">합계</span>
          <span className="font-bold text-primary">{formatKrw(calc.total_krw)}</span>
        </div>
      )}
      {calc.note && (
        <p className="text-[11px] text-[color:var(--color-ink-mute)] leading-relaxed pt-1">※ {calc.note}</p>
      )}
    </div>
  );
}

// 사용자가 빈 상태에서 클릭만으로 시작할 수 있게 하는 starter prompts.
// 너무 많이 넣으면 선택 부담이 커지므로 가장 자주 묻는 4 개만 노출한다.
const STARTER_PROMPTS = [
  '소농직불금 받으려면 무엇이 필요해요?',
  '청년농인데 받을 수 있는 직불금이 있나요?',
  '농업경영체 등록은 어떻게 하나요?',
  '부정수급 걸리면 어떻게 되나요?',
];

function AskModal({ onClose }: { onClose: () => void }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);

  // 메시지 추가 + 스트리밍 중 텍스트 누적에도 자동 스크롤. 마지막 메시지의 길이를
  // 트리거에 포함해 streaming delta 마다 따라 내려가게 한다 (새 메시지에만 점프하면
  // 답변이 길 때 사용자가 답변의 처음만 보이고 끝은 화면 밖에 갇히는 문제).
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const lastContentLen = messages[messages.length - 1]?.content.length ?? 0;
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [messages.length, lastContentLen]);

  const sendQuestion = async (raw: string) => {
    const question = raw.trim();
    if (!question || loading) return;

    // 1) Optimistic — user 메시지 + 빈 streaming assistant 메시지를 즉시 추가.
    //    history 에는 사용자 메시지 직전까지만 보내야 하므로 snapshot 을 따로 둔다.
    const historySnapshot = messages;
    const userMsg: ChatMessage = { role: 'user', content: question };
    const initialAssistantMsg: ChatMessage = {
      role: 'assistant',
      content: '',
      isStreaming: true,
    };
    setMessages([...historySnapshot, userMsg, initialAssistantMsg]);
    setInput('');
    setLoading(true);

    try {
      // 2) SSE 스트림 소비. delta 마다 마지막 메시지(=streaming assistant)에 텍스트 누적.
      //    done/error 는 stream 종료 시점에 한 번만 도착.
      for await (const ev of askSubsidyStream(question, { history: historySnapshot })) {
        if (ev.type === 'delta') {
          setMessages((prev) => {
            const updated = prev.slice();
            const last = updated[updated.length - 1];
            if (last?.role === 'assistant' && last.isStreaming) {
              updated[updated.length - 1] = { ...last, content: last.content + ev.content };
            }
            return updated;
          });
        } else if (ev.type === 'done') {
          setMessages((prev) => {
            const updated = prev.slice();
            const last = updated[updated.length - 1];
            if (last?.role === 'assistant') {
              updated[updated.length - 1] = {
                ...last,
                isStreaming: false,
                citations: ev.citations,
                escalationNeeded: ev.escalation_needed,
                faithfulnessWarnings: ev.faithfulness_warnings,
              };
            }
            return updated;
          });
        } else if (ev.type === 'error') {
          // 부분 답변은 유지하되 마지막에 에러 표시. streaming flag 만 닫고 toast.
          setMessages((prev) => {
            const updated = prev.slice();
            const last = updated[updated.length - 1];
            if (last?.role === 'assistant') {
              updated[updated.length - 1] = {
                ...last,
                isStreaming: false,
                content: last.content
                  ? `${last.content}\n\n[답변 중단: ${ev.message}]`
                  : `답변을 생성하지 못했습니다 (${ev.message}). 잠시 후 다시 시도해주세요.`,
              };
            }
            return updated;
          });
          toast.error(`질문 처리 실패: ${ev.message}`);
        }
      }
    } catch (err: unknown) {
      // fetch 자체 실패 (HTTP 4xx/5xx, 네트워크 단절 등). assistant 자리에 에러 안내.
      const msg = err instanceof Error ? err.message : '알 수 없는 오류';
      toast.error(`질문 처리 실패: ${msg}`);
      setMessages((prev) => {
        const updated = prev.slice();
        const last = updated[updated.length - 1];
        if (last?.role === 'assistant') {
          updated[updated.length - 1] = {
            ...last,
            isStreaming: false,
            content: `답변을 생성하지 못했습니다 (${msg}). 잠시 후 다시 시도해주세요.`,
          };
        }
        return updated;
      });
    } finally {
      setLoading(false);
    }
  };

  const handleSend = () => sendQuestion(input);

  const resetThread = () => {
    if (loading) return;
    if (messages.length === 0) return;
    if (window.confirm('대화를 모두 지우고 새로 시작할까요?')) {
      setMessages([]);
      setInput('');
    }
  };

  // 닫기 확인: 답변이 이미 표시되어 있거나, 입력창에 질문이 작성 중이거나,
  // LLM 호출이 진행 중일 때만 한 번 더 확인한다. 빈 상태에서는 바로 닫는다.
  const requestClose = () => {
    const hasUnsavedInput = input.trim().length > 0;
    const hasMessages = messages.length > 0;
    if (!loading && !hasUnsavedInput && !hasMessages) {
      onClose();
      return;
    }
    const msg = loading
      ? '답변 생성 중입니다. 지금 닫으면 결과를 볼 수 없습니다. 닫으시겠어요?'
      : '현재 진행 중인 대화가 있습니다. 창을 닫으시겠어요?';
    if (window.confirm(msg)) onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={requestClose}
    >
      <div
        className="bg-white rounded-2xl w-full max-w-2xl h-[90vh] sm:h-[85vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-5 border-b border-[color:var(--color-line-soft)] flex-shrink-0">
          <div className="flex items-center gap-2">
            <div className="w-9 h-9 rounded-full bg-primary/10 flex items-center justify-center text-primary">
              <MdSmartToy className="text-xl" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-[color:var(--color-ink)] leading-tight">
                시행지침 챗봇
              </h2>
              <p className="text-xs text-[color:var(--color-ink-mute)]">
                2026년도 기본형 공익직불사업
              </p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={resetThread}
              disabled={loading || messages.length === 0}
              className="text-[color:var(--color-ink-faint)] hover:text-[color:var(--color-ink-soft)] disabled:opacity-30 disabled:cursor-not-allowed p-1"
              title="새 대화"
            >
              <MdRefresh className="text-2xl" />
            </button>
            <button onClick={requestClose} className="text-[color:var(--color-ink-faint)] hover:text-[color:var(--color-ink-soft)] p-1">
              <MdClose className="text-2xl" />
            </button>
          </div>
        </div>

        <div ref={scrollRef} className="flex-1 overflow-y-auto p-5 space-y-4">
          {messages.length === 0 && !loading ? (
            <EmptyState onPick={sendQuestion} />
          ) : (
            messages.map((m, i) => <ChatBubble key={i} message={m} />)
          )}
        </div>

        <div className="p-4 border-t border-[color:var(--color-line-soft)] flex gap-2 flex-shrink-0">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
            placeholder={
              messages.length === 0
                ? '질문을 입력하거나 위의 예시를 눌러보세요'
                : '이어서 질문하세요'
            }
            className="flex-1 px-4 py-2.5 border border-[color:var(--color-line)] rounded-xl focus:outline-none focus:border-primary"
            disabled={loading}
          />
          <button
            onClick={handleSend}
            disabled={loading || !input.trim()}
            className="bg-primary text-white px-5 py-2.5 rounded-xl disabled:opacity-50 flex items-center gap-1 hover:bg-primary/90 transition"
          >
            <MdSend />
            <span className="hidden sm:inline">전송</span>
          </button>
        </div>
      </div>
    </div>
  );
}

// 비어 있을 때 보여주는 인사말 + 빠른 질문 chips.
function EmptyState({ onPick }: { onPick: (q: string) => void }) {
  return (
    <div className="py-6 space-y-5">
      <div className="text-center space-y-2">
        <div className="inline-flex w-12 h-12 rounded-full bg-primary/10 items-center justify-center text-primary">
          <MdSmartToy className="text-2xl" />
        </div>
        <p className="text-[color:var(--color-ink-soft)] font-semibold">
          공익직불사업에 관한 무엇이든 물어보세요
        </p>
        <p className="text-xs text-[color:var(--color-ink-mute)]">
          시행지침 본문을 근거로 답변합니다. 답변 끝에는 출처 조항이 함께 표시됩니다.
        </p>
      </div>
      <div className="space-y-2">
        <p className="text-xs font-semibold text-[color:var(--color-ink-mute)] px-1">자주 묻는 질문</p>
        <div className="flex flex-col gap-2">
          {STARTER_PROMPTS.map((q) => (
            <button
              key={q}
              onClick={() => onPick(q)}
              className="text-left text-sm text-[color:var(--color-ink-soft)] bg-[color:var(--color-surface)] hover:bg-primary/5 hover:text-primary border border-[color:var(--color-line)] hover:border-primary/30 rounded-xl px-3 py-2.5 transition"
            >
              {q}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// 한 메시지 = 한 말풍선. user 는 오른쪽, assistant 는 왼쪽 + citations 동봉.
function ChatBubble({ message }: { message: ChatMessage }) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="flex items-start gap-2 max-w-[85%]">
          <div className="bg-primary text-white rounded-2xl rounded-tr-sm px-4 py-2.5 shadow-sm">
            <p className="text-sm whitespace-pre-wrap leading-relaxed">{message.content}</p>
          </div>
          <div className="w-7 h-7 rounded-full bg-primary/15 flex items-center justify-center text-primary flex-shrink-0 mt-0.5">
            <MdPerson className="text-base" />
          </div>
        </div>
      </div>
    );
  }
  // streaming 중: 빈 content 면 "타이핑 중" 도트 3개, content 가 있으면 마지막에 caret.
  const isStreaming = Boolean(message.isStreaming);
  const hasContent = message.content.length > 0;
  return (
    <div className="flex justify-start">
      <div className="flex items-start gap-2 max-w-[90%]">
        <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center text-primary flex-shrink-0 mt-0.5">
          <MdSmartToy className="text-base" />
        </div>
        <div className="space-y-2 flex-1 min-w-0">
          <div className="bg-[color:var(--color-surface)] border border-[color:var(--color-line)] rounded-2xl rounded-tl-sm px-4 py-2.5">
            {isStreaming && !hasContent ? (
              <span className="flex items-center gap-1.5 py-1" role="status" aria-live="polite">
                <span className="w-2 h-2 rounded-full bg-gray-400 animate-pulse" style={{ animationDelay: '0ms' }} />
                <span className="w-2 h-2 rounded-full bg-gray-400 animate-pulse" style={{ animationDelay: '150ms' }} />
                <span className="w-2 h-2 rounded-full bg-gray-400 animate-pulse" style={{ animationDelay: '300ms' }} />
              </span>
            ) : (
              <p className="text-sm text-[color:var(--color-ink)] whitespace-pre-wrap leading-relaxed">
                {message.content}
                {isStreaming && (
                  <span
                    aria-hidden
                    className="inline-block w-[2px] h-4 bg-primary/70 align-middle ml-0.5 animate-pulse"
                  />
                )}
              </p>
            )}
          </div>
          {/* citations / warnings 는 streaming 끝난 뒤 done 이벤트에서만 채워짐. */}
          {message.citations && message.citations.length > 0 && (
            <CitationsSection citations={message.citations} />
          )}
          {message.faithfulnessWarnings && message.faithfulnessWarnings.length > 0 && (
            <FaithfulnessWarning tags={message.faithfulnessWarnings} />
          )}
          {message.escalationNeeded && (
            <div className="bg-[color:var(--tint-warning)] border border-amber-200 rounded-xl p-3 text-xs text-amber-800">
              시행지침만으로 답변하기 어려운 질문입니다.
              농관원 콜센터(1334) 또는 지자체 담당자에게 문의해주세요.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// 답변 본문에 등장하는 조항 태그 중 검색된 citation 으로 검증되지 않는 것이 있을 때
// 사용자에게 명시적으로 알린다. 답변을 차단하지 않고 투명하게 경고만 — 사용자가 직접
// 출처를 확인하도록 유도. 빨강 (위험) 보다 한 단계 부드러운 amber 톤으로 표시.
function FaithfulnessWarning({ tags }: { tags: string[] }) {
  return (
    <div className="bg-[color:var(--tint-warning)] border border-amber-200 rounded-xl p-3 text-xs text-amber-900">
      <p className="font-semibold mb-1 flex items-center gap-1.5">
        <MdWarning className="text-[color:var(--color-accent-dark)]" /> 검증되지 않은 인용 발견
      </p>
      <p className="leading-relaxed">
        답변에 다음 조항 태그가 등장했지만 검색된 시행지침 조항과 일치하지 않습니다:{' '}
        <span className="font-mono font-semibold">{tags.join(', ')}</span>.
        실제 시행지침에서 다시 한 번 확인하거나, 농관원(1334) 또는 지자체 담당자에게 문의해주세요.
      </p>
    </div>
  );
}

// ─── 근거 조항 섹션 (접을 수 있는 citation 카드) ─────────

function CitationsSection({ citations }: { citations: Citation[] }) {
  // 백엔드 dedup 이 안 된 경우를 대비한 프론트 방어 로직 (중복 article 제거)
  const deduped = useMemo(() => {
    const seen = new Set<string>();
    const out: Citation[] = [];
    for (const c of citations) {
      const key = `${c.chapter}__${c.article}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(c);
    }
    return out.slice(0, 3);
  }, [citations]);

  return (
    <div>
      <p className="text-xs font-semibold text-[color:var(--color-ink-mute)] mb-2">
        근거 조항 ({deduped.length}건)
      </p>
      <div className="space-y-2">
        {deduped.map((c, i) => (
          <CitationCard key={`${c.chapter}-${c.article}-${i}`} c={c} />
        ))}
      </div>
    </div>
  );
}

function CitationCard({ c }: { c: Citation }) {
  const [open, setOpen] = useState(false);

  // 백엔드 snippet 이 아직 정리되지 않은 경우를 대비한 프론트 방어 로직
  const cleanSnippet = useMemo(() => cleanSnippetFallback(c.snippet), [c.snippet]);
  const cleanChapter = useMemo(
    () => c.chapter.replace(/\s*>\s*\(장 내 최상위 절\)|\s*>\s*\(상위 미지정\)/g, ''),
    [c.chapter],
  );
  const hasSnippet = Boolean(cleanSnippet);

  return (
    <div className="bg-[color:var(--color-surface)] border border-[color:var(--color-line)] rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => hasSnippet && setOpen(!open)}
        className={`w-full text-left px-3 py-2.5 flex items-start gap-2 ${
          hasSnippet ? 'hover:bg-[color:var(--color-surface-deep)] cursor-pointer' : 'cursor-default'
        }`}
        aria-expanded={open}
      >
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-[color:var(--color-ink)] truncate">
            {c.article || '(제목 없음)'}
          </p>
          {cleanChapter && (
            <p className="text-xs text-[color:var(--color-ink-mute)] mt-0.5 truncate">{cleanChapter}</p>
          )}
        </div>
        {hasSnippet && (
          <span className="flex-shrink-0 text-[color:var(--color-ink-faint)] text-lg mt-0.5">
            {open ? <MdExpandLess /> : <MdExpandMore />}
          </span>
        )}
      </button>
      {open && hasSnippet && (
        <div className="px-3 pb-3 pt-2 border-t border-[color:var(--color-line)] bg-white">
          <p className="text-xs text-[color:var(--color-ink-soft)] leading-relaxed">{cleanSnippet}</p>
        </div>
      )}
    </div>
  );
}

// 출처 섹션의 한 항목 — 태그+요약을 보여 주고, 클릭 시 시행지침 원문 발췌(snippet)를 펼친다.
// AskModal 의 CitationCard 와 같은 인터랙션 모델을 따른다.
// `loading=true` 면 chevron 자리에 작은 spinner 를 표시하고 클릭 비활성.
function SourceClauseCard({
  clause,
  loading = false,
}: {
  clause: SourceClause;
  loading?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const cleanSnippet = useMemo(
    () => cleanSnippetFallback(clause.snippet ?? undefined),
    [clause.snippet],
  );
  const hasSnippet = Boolean(cleanSnippet);
  const interactive = hasSnippet && !loading;

  return (
    <li className="bg-[color:var(--color-surface)] border border-[color:var(--color-line)] rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => interactive && setOpen(!open)}
        className={`w-full text-left px-3 py-2 flex items-start gap-2 text-sm text-[color:var(--color-ink-soft)] ${
          interactive ? 'hover:bg-[color:var(--color-surface-deep)] cursor-pointer' : 'cursor-default'
        }`}
        aria-expanded={open}
        disabled={!interactive}
      >
        <span className="inline-flex items-center text-[11px] font-bold px-2 py-0.5 rounded-md bg-primary text-white shadow-sm whitespace-nowrap flex-shrink-0 mt-0.5">
          {clause.tag}
        </span>
        <span className="flex-1 leading-relaxed">{clause.text}</span>
        {loading ? (
          <span
            className="flex-shrink-0 mt-1 w-3 h-3 rounded-full border-2 border-[color:var(--color-line)] border-t-primary animate-spin"
            aria-label="원문 불러오는 중"
          />
        ) : (
          hasSnippet && (
            <span className="flex-shrink-0 text-[color:var(--color-ink-faint)] text-lg mt-0.5" aria-hidden>
              {open ? <MdExpandLess /> : <MdExpandMore />}
            </span>
          )
        )}
      </button>
      {open && hasSnippet && (
        <div className="px-3 pb-3 pt-2 border-t border-[color:var(--color-line)] bg-white">
          <p className="text-[11px] font-semibold text-[color:var(--color-ink-mute)] uppercase tracking-wide mb-1">
            시행지침 원문
          </p>
          <p className="text-xs text-[color:var(--color-ink-soft)] leading-relaxed">{cleanSnippet}</p>
        </div>
      )}
    </li>
  );
}

/** 백엔드 정리가 안 된 snippet 을 프론트에서도 한 번 더 털어냄 (방어 로직). */
function cleanSnippetFallback(raw: string | undefined): string {
  if (!raw) return '';
  return raw
    .replace(/\|\s*-{3,}\s*(?:\|\s*-{3,}\s*)+\|/g, ' ')   // table separator rows
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')                  // image placeholders
    .replace(/^#{1,6}\s*/gm, '')                            // markdown headers
    .replace(/[☑□]/g, '')                                    // check-box chars
    .replace(/\s*\|\s*/g, ' · ')                             // remaining pipes → middle dot
    .replace(/(?:\s*·\s*){2,}/g, ' · ')                      // collapse consecutive dots
    .replace(/\s+/g, ' ')                                     // collapse whitespace
    .replace(/^[\s·]+|[\s·]+$/g, '')                         // trim dots/spaces
    .slice(0, 350);
}
