import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useChatSessions, useSessionLogs } from '@/admin/hooks/useChatbot';
import { useUserExchangeTickets } from '@/admin/hooks/useTickets';
import { INTENT_LABEL, INTENT_COLOR_BADGE as INTENT_COLOR } from '@/admin/constants/chatbot';
import { TICKET_STATUS_LABEL, TICKET_ACTION_LABEL, TICKET_ACTION_COLOR } from '@/admin/types/ticket';
import { formatDate } from '@/lib/utils';
import type { ChatSession } from '@/admin/types/chatlog';
import type { Ticket } from '@/admin/types/ticket';

// ──────────────────────────────────────────
// RelatedTickets
// ──────────────────────────────────────────

/** 교환 intent 챗로그에서 연관 티켓을 표시하는 컴포넌트 */
function RelatedTickets({ tickets, isLoading }: { tickets: Ticket[]; isLoading: boolean }) {
  if (isLoading) {
    return (
      <p className="text-xs text-stone-400 py-2">관련 티켓 조회 중...</p>
    );
  }

  if (tickets.length === 0) {
    return (
      <p className="text-xs text-stone-400 py-2">교환 티켓 없음</p>
    );
  }

  return (
    <div className="space-y-3">
      {tickets.map((ticket) => {
        const isCompleted = ticket.status === 'completed' || ticket.status === 'cancelled';
        return (
          <div
            key={ticket.id}
            className={`p-4 rounded-xl border-l-4 ${
              isCompleted
                ? 'bg-stone-50 border-l-stone-300'
                : 'bg-surface-container-low border-l-emerald-500'
            }`}
          >
            <div className="flex justify-between items-start mb-2">
              <span
                className={`text-[10px] font-black px-2 py-0.5 rounded uppercase ${TICKET_ACTION_COLOR[ticket.action_type]}`}
              >
                {TICKET_ACTION_LABEL[ticket.action_type]}
              </span>
              <span className="text-[10px] text-stone-500 font-medium">
                #{ticket.id}
              </span>
            </div>
            <p className="text-xs font-bold text-on-surface mb-2 truncate">
              {ticket.reason}
            </p>
            <div className="flex items-center justify-between">
              <span
                className={`text-[10px] font-bold flex items-center gap-1 ${
                  isCompleted ? 'text-stone-500' : 'text-emerald-700'
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    isCompleted ? 'bg-stone-400' : 'bg-emerald-500'
                  }`}
                />
                {TICKET_STATUS_LABEL[ticket.status]}
              </span>
              <NavLink
                to={`/admin/tickets?ticketId=${ticket.id}`}
                className="text-[10px] font-bold text-primary hover:underline"
              >
                상세보기
              </NavLink>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ──────────────────────────────────────────
// ChatbotPage
// ──────────────────────────────────────────

export default function ChatbotPage() {
  const [tab, setTab] = useState<'all' | 'escalated'>('all');
  const [selectedSession, setSelectedSession] = useState<ChatSession | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const { data: allSessions = [], isLoading: loadingAll } = useChatSessions(false);
  const { data: escalatedSessions = [], isLoading: loadingEsc } = useChatSessions(true);
  const { data: sessionLogs = [], isLoading: loadingLogs } = useSessionLogs(selectedSession?.id ?? null);
  const { data: selectedUserTickets = [], isLoading: loadingSelectedTickets } = useUserExchangeTickets(selectedSession?.user_id ?? null);

  // 선택된 세션에서 발생한 미처리 티켓 — 상세 헤더 배너용
  const pendingSelectedTickets = selectedUserTickets.filter(
    (t) => t.session_id === selectedSession?.id &&
      (t.status === 'received' || t.status === 'processing'),
  );

  const isLoading = tab === 'all' ? loadingAll : loadingEsc;
  const sessions = tab === 'all' ? allSessions : escalatedSessions;

  // Track previous log date for date separators
  let prevDateStr = '';

  return (
    <div className="h-[calc(100vh-4rem)] flex overflow-hidden">

      {/* ────── Left Panel: Session List ────── */}
      <aside className="w-[288px] shrink-0 border-r border-zinc-100 flex flex-col bg-surface">

        {/* Tab switcher */}
        <div className="p-4 border-b border-zinc-100">
          <div className="flex gap-2 p-1 bg-surface-container rounded-xl">
            <button
              type="button"
              onClick={() => { setTab('all'); setSelectedSession(null); }}
              className={`flex-1 py-1.5 text-xs font-semibold rounded-lg transition-colors ${
                tab === 'all'
                  ? 'bg-white shadow-sm text-on-surface'
                  : 'text-stone-500 hover:bg-white/50'
              }`}
              aria-pressed={tab === 'all'}
            >
              전체 대화
            </button>
            <button
              type="button"
              onClick={() => { setTab('escalated'); setSelectedSession(null); }}
              className={`flex-1 py-1.5 text-xs font-medium rounded-lg transition-colors flex items-center justify-center gap-1.5 ${
                tab === 'escalated'
                  ? 'bg-white shadow-sm text-on-surface'
                  : 'text-stone-500 hover:bg-white/50'
              }`}
              aria-pressed={tab === 'escalated'}
            >
              에스컬레이션
              {escalatedSessions.length > 0 && (
                <span className="bg-error text-white text-[10px] px-1.5 rounded-full leading-none py-0.5">
                  {escalatedSessions.length}
                </span>
              )}
            </button>
          </div>
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-y-auto">
          {isLoading ? (
            Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="p-4 border-b border-stone-50 animate-pulse space-y-2" aria-hidden="true">
                <div className="h-3 w-20 bg-stone-200 rounded" />
                <div className="h-3 w-40 bg-stone-100 rounded" />
              </div>
            ))
          ) : sessions.length === 0 ? (
            <div className="p-6 text-center text-stone-400 text-sm">대화 내역이 없습니다.</div>
          ) : (
            sessions.map((session) => {
              const preview = session.title
                ?? (session.last_question ? session.last_question.slice(0, 40) : '내용 없음');
              return (
                <button
                  key={session.id}
                  type="button"
                  onClick={() => setSelectedSession(session)}
                  className={`w-full text-left p-4 transition-all ${
                    selectedSession?.id === session.id
                      ? 'bg-primary-container/5 border-b border-primary/10'
                      : 'hover:bg-stone-100/50 border-b border-stone-50'
                  }`}
                  aria-pressed={selectedSession?.id === session.id}
                >
                  {/* Row 1: user ID + dots + time */}
                  <div className="flex justify-between items-start mb-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold text-on-surface">
                        회원 #{session.user_id}
                      </span>
                      {session.has_escalation && (
                        <span
                          className="w-1.5 h-1.5 rounded-full bg-error animate-pulse shrink-0"
                          title="에스컬레이션"
                          aria-label="에스컬레이션"
                        />
                      )}
                      {session.pending_ticket_status && (
                        <span
                          className={`text-[9px] font-black px-1.5 py-0.5 rounded uppercase tracking-tight shrink-0 ${
                            session.pending_ticket_status === 'received'
                              ? 'bg-amber-100 text-amber-700'
                              : 'bg-sky-100 text-sky-700'
                          }`}
                          title={session.pending_ticket_status === 'received' ? '미접수 티켓 있음' : '처리 중인 티켓 있음'}
                        >
                          Ticket
                        </span>
                      )}
                    </div>
                    <span className="text-[10px] text-stone-400 font-medium shrink-0">
                      {session.last_message_at
                        ? formatDate(session.last_message_at, 'MM/dd HH:mm')
                        : formatDate(session.created_at, 'MM/dd HH:mm')}
                    </span>
                  </div>

                  {/* Row 2: title / question preview */}
                  <p className="text-xs text-stone-600 line-clamp-1 mb-1">{preview}</p>

                  {/* Row 3: message count badge + status */}
                  <div className="flex items-center gap-2">
                    <span className="px-1.5 py-0.5 bg-stone-100 text-stone-500 text-[10px] font-bold rounded">
                      {session.log_count}개 메시지
                    </span>
                    <span className="text-[10px] text-stone-400 font-medium">
                      {session.status}
                    </span>
                  </div>
                </button>
              );
            })
          )}
        </div>
      </aside>

      {/* ────── Main Panel: Session Detail ────── */}
      <section className="flex-1 flex flex-col bg-surface-container-low min-w-0">
        {selectedSession ? (
          <>
            {/* Detail header */}
            <div className="p-6 bg-surface-container-lowest border-b border-stone-100 shrink-0">
              <div className="flex justify-between items-start mb-4">
                <div>
                  {/* Row 1: user ID + message count */}
                  <div className="flex items-center gap-3 mb-1">
                    <h2 className="text-xl font-black text-on-surface">
                      회원 #{selectedSession.user_id}
                    </h2>
                    <span className="text-[11px] text-stone-400 font-medium">
                      {selectedSession.log_count}개 메시지
                    </span>
                  </div>
                  {/* Session start date */}
                  <p className="text-sm text-stone-500 font-medium">
                    채팅 시작 · {formatDate(selectedSession.created_at)}
                  </p>
                </div>

                {/* Action buttons */}
                <div className="flex gap-2 shrink-0">
                  <button
                    type="button"
                    className="bg-stone-100 text-stone-600 px-4 py-2 rounded-xl text-sm font-bold hover:bg-stone-200 transition-colors"
                  >
                    이력 보기
                  </button>
                  <button
                    type="button"
                    className="bg-primary text-on-primary px-4 py-2 rounded-xl text-sm font-bold shadow-lg shadow-primary/20 hover:opacity-90 transition-opacity"
                  >
                    직접 상담 전환
                  </button>
                </div>
              </div>

              {/* Escalation warning banner */}
              {selectedSession.has_escalation && (
                <div className="flex items-center gap-3 p-3 bg-error-container/30 border border-error/10 rounded-xl">
                  <span
                    className="material-symbols-outlined text-error text-[20px]"
                    aria-hidden="true"
                  >
                    report
                  </span>
                  <p className="text-sm font-semibold text-on-error-container">
                    해당 대화는 AI 처리 한계를 초과하여 에스컬레이션되었습니다. 상담원 개입이 권장됩니다.
                  </p>
                </div>
              )}

              {/* Pending ticket warning banner */}
              {pendingSelectedTickets.length > 0 && (
                <div className="flex items-center gap-3 p-3 bg-amber-50 border border-amber-200 rounded-xl">
                  <span
                    className="material-symbols-outlined text-amber-500 text-[20px] shrink-0"
                    aria-hidden="true"
                  >
                    assignment_late
                  </span>
                  <p className="text-sm font-semibold text-amber-800">
                    처리되지 않은 티켓이 {pendingSelectedTickets.length}건 있습니다.
                  </p>
                  <NavLink
                    to={`/admin/tickets?userId=${selectedSession.user_id}`}
                    className="ml-auto text-xs font-bold text-amber-700 hover:underline shrink-0"
                  >
                    티켓 보기
                  </NavLink>
                </div>
              )}
            </div>

            {/* Chat area */}
            <div className="flex-1 overflow-y-auto p-6 space-y-6">
              {loadingLogs ? (
                <div className="text-center text-stone-400 text-sm py-8">
                  대화 내역을 불러오는 중...
                </div>
              ) : sessionLogs.length === 0 ? (
                <div className="h-full flex items-center justify-center">
                  <p className="text-sm text-stone-400 font-medium">대화 내역이 없습니다.</p>
                </div>
              ) : (
                (() => {
                  prevDateStr = '';
                  return sessionLogs.map((log) => {
                    const logDateStr = formatDate(log.created_at, 'yyyy-MM-dd');
                    const showDateSep = logDateStr !== prevDateStr;
                    prevDateStr = logDateStr;

                    return (
                      <div key={log.id} className="space-y-6">
                        {/* Date separator */}
                        {showDateSep && (
                          <div className="flex justify-center">
                            <span className="bg-stone-200/50 text-stone-500 text-[10px] font-bold px-3 py-1 rounded-full uppercase tracking-widest">
                              {formatDate(log.created_at, 'yyyy년 MM월 dd일')}
                            </span>
                          </div>
                        )}

                        {/* User message bubble */}
                        <div className="flex justify-end">
                          <div className="bg-primary text-on-primary p-4 rounded-xl rounded-tr-none shadow-lg shadow-primary/10 max-w-[80%]">
                            <p className="text-sm leading-relaxed">{log.question}</p>
                            <span className="block text-[10px] text-primary-fixed/70 mt-2 text-right">
                              {formatDate(log.created_at, 'HH:mm')}
                            </span>
                          </div>
                        </div>

                        {/* Bot message bubble */}
                        <div className="flex gap-4 items-start">
                          <div
                            className="w-8 h-8 rounded-full bg-primary-container flex items-center justify-center text-on-primary-container shadow-sm shrink-0"
                            aria-hidden="true"
                          >
                            <span className="material-symbols-outlined text-[18px]">smart_toy</span>
                          </div>
                          <div className="space-y-1.5 max-w-[80%]">
                            <div className="bg-surface-container-lowest p-4 rounded-xl rounded-tl-none shadow-sm">
                              <p className="text-sm text-on-surface leading-relaxed whitespace-pre-wrap">
                                {log.answer}
                              </p>
                            </div>
                            {/* Intent badge */}
                            <span
                              className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-tighter ${
                                INTENT_COLOR[log.intent] ?? 'bg-gray-100 text-gray-600'
                              }`}
                            >
                              {INTENT_LABEL[log.intent] ?? log.intent}
                            </span>
                          </div>
                        </div>

                        {/* Rating section */}
                        {log.rating != null && (
                          <div className="flex flex-col items-center gap-3 pt-2">
                            <p className="text-xs font-bold text-stone-400 uppercase tracking-widest">
                              상담 품질 피드백
                            </p>
                            <div className="flex gap-1" aria-label={`평점 ${log.rating}점`}>
                              {Array.from({ length: 5 }, (_, i) => (
                                <span
                                  key={i}
                                  className={`material-symbols-outlined text-[22px] ${
                                    i < log.rating! ? 'text-primary' : 'text-stone-300'
                                  }`}
                                  style={i < log.rating! ? { fontVariationSettings: "'FILL' 1" } : undefined}
                                  aria-hidden="true"
                                >
                                  star
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  });
                })()
              )}
            </div>

            {/* Input area (display-only) */}
            <div className="p-4 bg-surface-container-lowest border-t border-stone-100 shrink-0">
              <div className="bg-stone-100 rounded-xl px-4 py-2 flex items-center gap-4">
                <button
                  type="button"
                  className="text-stone-400 hover:text-primary transition-colors"
                  aria-label="첨부"
                  disabled
                >
                  <span className="material-symbols-outlined text-[22px]">add_circle</span>
                </button>
                <span className="flex-1 text-sm text-stone-400 py-2 select-none">
                  직접 메시지를 입력하세요...
                </span>
                <button
                  type="button"
                  className="text-primary"
                  aria-label="전송"
                  disabled
                >
                  <span
                    className="material-symbols-outlined text-[22px]"
                    style={{ fontVariationSettings: "'FILL' 1" }}
                  >
                    send
                  </span>
                </button>
              </div>
            </div>
          </>
        ) : (
          /* Empty state */
          <div className="h-full flex items-center justify-center">
            <div className="text-center space-y-3">
              <span
                className="material-symbols-outlined text-stone-200 text-[64px] block"
                style={{ fontVariationSettings: "'FILL' 1" }}
                aria-hidden="true"
              >
                chat_bubble
              </span>
              <p className="text-sm text-stone-400 font-medium">목록에서 대화를 선택하세요</p>
            </div>
          </div>
        )}
      </section>

      {/* ────── Right Sidebar: Context Info ────── */}
      <aside className={`shrink-0 bg-white border-l border-stone-100 flex flex-col transition-[width] duration-200 overflow-hidden ${sidebarOpen ? 'w-[320px]' : 'w-10'}`}>
        {/* Toggle button */}
        <button
          type="button"
          onClick={() => setSidebarOpen((prev) => !prev)}
          className="flex items-center justify-start px-2 h-10 w-full shrink-0 border-b border-stone-100 text-stone-400 hover:text-stone-600 hover:bg-stone-50 transition-colors"
          aria-label={sidebarOpen ? '사이드바 닫기' : '사이드바 열기'}
          aria-expanded={sidebarOpen}
        >
          <span
            className={`material-symbols-outlined text-[18px] transition-transform duration-200 ${sidebarOpen ? 'rotate-0' : 'rotate-180'}`}
            aria-hidden="true"
          >
            chevron_right
          </span>
        </button>

        <div className={`flex-1 overflow-y-auto p-6 space-y-8 ${sidebarOpen ? '' : 'hidden'}`}>

        {selectedSession ? (
          <>
            {/* Section 1: Related tickets */}
            <div>
              <h3 className="text-sm font-bold text-on-surface mb-4 flex items-center gap-2">
                <span
                  className="material-symbols-outlined text-emerald-600 text-[20px]"
                  aria-hidden="true"
                >
                  inventory_2
                </span>
                연관 티켓 정보
              </h3>

              {sessionLogs.some((l) => l.intent === 'exchange') ? (
                <RelatedTickets
                  tickets={selectedUserTickets.filter((t) => t.session_id === selectedSession.id)}
                  isLoading={loadingSelectedTickets}
                />
              ) : (
                <p className="text-xs text-stone-400 py-2">
                  해당 인텐트에는 연관 티켓이 없습니다.
                </p>
              )}
            </div>

            {/* Section 2: User summary */}
            <div>
              <h3 className="text-sm font-bold text-on-surface mb-4 flex items-center gap-2">
                <span
                  className="material-symbols-outlined text-emerald-600 text-[20px]"
                  aria-hidden="true"
                >
                  account_circle
                </span>
                회원 요약 정보
              </h3>

              <div className="space-y-4">
                {/* Avatar + name */}
                <div className="flex items-center gap-4">
                  <div
                    className="w-12 h-12 rounded-xl bg-secondary-container flex items-center justify-center text-on-secondary-container shrink-0"
                    aria-hidden="true"
                  >
                    <span className="material-symbols-outlined text-2xl">person</span>
                  </div>
                  <div>
                    <p className="text-sm font-bold text-on-surface">
                      회원 #{selectedSession.user_id}
                    </p>
                    <p className="text-xs text-stone-500">
                      {formatDate(selectedSession.created_at, 'yyyy.MM.dd')} 세션 시작
                    </p>
                  </div>
                </div>

                {/* Stats grid */}
                <div className="grid grid-cols-2 gap-3">
                  <div className="bg-stone-50 p-3 rounded-lg">
                    <p className="text-[10px] text-stone-400 font-bold uppercase tracking-tighter mb-1">
                      메시지 수
                    </p>
                    <p className="text-sm font-bold text-on-surface">
                      {selectedSession.log_count}개
                    </p>
                  </div>
                  <div className="bg-stone-50 p-3 rounded-lg">
                    <p className="text-[10px] text-stone-400 font-bold uppercase tracking-tighter mb-1">
                      에스컬레이션 여부
                    </p>
                    <p
                      className={`text-sm font-bold ${
                        selectedSession.has_escalation ? 'text-error' : 'text-emerald-700'
                      }`}
                    >
                      {selectedSession.has_escalation ? '예' : '아니오'}
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </>
        ) : (
          /* Right sidebar empty state */
          <div className="h-full flex items-center justify-center">
            <p className="text-xs text-stone-300 text-center">
              대화를 선택하면<br />상세 정보가 표시됩니다.
            </p>
          </div>
        )}
        </div>
      </aside>
    </div>
  );
}
