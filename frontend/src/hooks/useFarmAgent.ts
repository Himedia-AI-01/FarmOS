import { useCallback, useEffect, useRef, useState } from 'react';
import { FARMOS_API_BASE } from '@/lib/api';

// ── 에이전틱 메시지 모델 ──────────────────────────────────────────────────────
//
// 메시지는 단순 텍스트가 아니라 "에이전트 활동 흐름"을 담는다:
//   - reasoning steps: write_todos 계획, 서브에이전트 디스패치
//   - tool calls: 입력/출력 페어로 카드 렌더링
//   - citations: [doc > 제N조] 칩
//   - actions: HITL 승인 카드 (IoT 제어 제안)
// 백엔드 SSE 이벤트는 farm_agent.py:/stream 참고.

export type ReasoningStep =
  | { kind: 'plan'; markdown: string }
  | { kind: 'subagent'; name: string };

export interface ToolCall {
  toolCallId: string;
  name: string;
  args?: unknown;
  output?: string;
}

export interface Citation {
  label: string;
  doc: string;
  snippet: string;
}

// Backend SSE `low_confidence` event payload — emitted when a subsidy-domain
// answer ships without any `[doc > 제N조]` citation. UI renders this as a soft
// warning chip; the bubble itself stays as-is (non-blocking guardrail).
// Source: ReasoningBank-style multi-dimensional process scoring.
// Domain literal union — narrowed from `string` so consumers' switch/branch
// logic on `low.domain` surfaces typos at compile time. Keep the trailing
// `string` fallback so a future backend value doesn't break the type at
// runtime (it just wouldn't match the literals).
export type LowConfidenceDomain = 'subsidy' | 'diagnosis' | 'general' | (string & {});

export interface LowConfidence {
  reason: string;
  domain: LowConfidenceDomain;
  hint: string;
}

export interface ActionProposal {
  kind: 'iot_control';
  controlType: 'ventilation' | 'irrigation' | 'lighting' | 'shading' | string;
  summary: string;
  status: 'pending' | 'approved' | 'rejected' | 'executing' | 'failed';
  detail?: string;
}

export interface FarmAgentMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  streaming?: boolean;
  fastPath?: boolean;
  steps: ReasoningStep[];
  toolCalls: ToolCall[];
  citations: Citation[];
  action?: ActionProposal;
  lowConfidence?: LowConfidence;
  attachments?: { kind: 'image'; url: string; alt?: string }[];
  error?: boolean;
}

export interface FarmAgentBriefing {
  date: string;
  content: string;
  cached: boolean;
}

export interface FarmAgentThread {
  session_id: string;
  last_user_message: string;
  updated_at: string | null;
  message_count: number;
}

type StreamEventHandler = (event: string, data: string) => void;

function makeId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function parseEventBlock(block: string, onEvent: StreamEventHandler) {
  let event = 'message';
  const dataLines: string[] = [];

  for (const rawLine of block.split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(':')) continue;
    if (rawLine.startsWith('event:')) {
      event = rawLine.slice(6).trim();
      continue;
    }
    if (rawLine.startsWith('data:')) {
      const value = rawLine.slice(5);
      dataLines.push(value.startsWith(' ') ? value.slice(1) : value);
    }
  }

  onEvent(event, dataLines.join('\n'));
}

async function consumeSseStream(response: Response, onEvent: StreamEventHandler) {
  if (!response.body) {
    throw new Error('스트림 응답을 읽을 수 없습니다.');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? '';

    for (const block of blocks) {
      parseEventBlock(block, onEvent);
    }
  }

  buffer += decoder.decode();
  if (buffer.trim()) {
    parseEventBlock(buffer, onEvent);
  }
}

function safeJson<T>(raw: string): T | null {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function useFarmAgent() {
  const [messages, setMessages] = useState<FarmAgentMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [briefing, setBriefing] = useState<FarmAgentBriefing | null>(null);
  const [briefingLoading, setBriefingLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [threads, setThreads] = useState<FarmAgentThread[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  // Synchronous busy mirror — useState updates are async, so two send/voice/image
  // calls fired within the same tick would both see `busy === false` and race.
  // Always check (and flip) busyRef before kicking off a new request.
  const busyRef = useRef(false);
  const setBusyBoth = useCallback((value: boolean) => {
    busyRef.current = value;
    setBusy(value);
  }, []);

  const fetchBriefing = useCallback(async (refresh = false) => {
    setBriefingLoading(true);
    try {
      const params = refresh ? '?refresh=true' : '';
      const res = await fetch(`${FARMOS_API_BASE}/farm-agent/briefing${params}`, {
        credentials: 'include',
      });
      if (!res.ok) throw new Error(`브리핑 생성 실패 (${res.status})`);
      const data = (await res.json()) as FarmAgentBriefing;
      setBriefing(data);
      setError(null);
      return data;
    } catch (err) {
      const message = err instanceof Error ? err.message : '브리핑을 불러올 수 없습니다.';
      setError(message);
      return null;
    } finally {
      setBriefingLoading(false);
    }
  }, []);

  const fetchThreads = useCallback(async () => {
    setThreadsLoading(true);
    try {
      const res = await fetch(`${FARMOS_API_BASE}/farm-agent/threads?limit=30`, {
        credentials: 'include',
      });
      if (!res.ok) return;
      const data = (await res.json()) as FarmAgentThread[];
      setThreads(Array.isArray(data) ? data : []);
    } catch {
      // 네트워크 실패 시 조용히 폴백 — 스레드 사이드바는 보조 기능
    } finally {
      setThreadsLoading(false);
    }
  }, []);

  const loadThread = useCallback(async (id: string) => {
    // Switching threads mid-stream: cancel any in-flight SSE so it stops mutating
    // messages we're about to replace.
    abortRef.current?.abort();
    abortRef.current = null;
    setBusyBoth(false);
    setError(null);
    try {
      const res = await fetch(`${FARMOS_API_BASE}/farm-agent/threads/${id}`, {
        credentials: 'include',
      });
      if (!res.ok) throw new Error(`스레드 복원 실패 (${res.status})`);
      const data = (await res.json()) as {
        session_id: string;
        messages: { role: 'user' | 'assistant'; content: string }[];
      };
      setSessionId(data.session_id);
      setMessages(
        data.messages.map((m, idx) => ({
          id: `${data.session_id}-${idx}`,
          role: m.role,
          content: m.content,
          steps: [],
          toolCalls: [],
          citations: [],
        })),
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : '스레드를 복원할 수 없습니다.';
      setError(message);
    }
  }, [setBusyBoth]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusyBoth(false);
    setMessages((prev) =>
      prev.map((message) =>
        message.streaming ? { ...message, streaming: false } : message,
      ),
    );
  }, [setBusyBoth]);

  const reset = useCallback(() => {
    stop();
    setMessages([]);
    setSessionId(null);
    setError(null);
  }, [stop]);

  const updateAssistant = useCallback(
    (id: string, mutate: (m: FarmAgentMessage) => FarmAgentMessage) => {
      setMessages((prev) => prev.map((m) => (m.id === id ? mutate(m) : m)));
    },
    [],
  );

  const send = useCallback(
    async (question: string, options?: { attachments?: FarmAgentMessage['attachments'] }) => {
      const trimmed = question.trim();
      if (!trimmed || busyRef.current) return;

      const controller = new AbortController();
      abortRef.current = controller;
      setBusyBoth(true);
      setError(null);

      const userMessage: FarmAgentMessage = {
        id: makeId('user'),
        role: 'user',
        content: trimmed,
        steps: [],
        toolCalls: [],
        citations: [],
        attachments: options?.attachments,
      };
      const assistantId = makeId('agent');
      const assistantMessage: FarmAgentMessage = {
        id: assistantId,
        role: 'assistant',
        content: '',
        streaming: true,
        steps: [],
        toolCalls: [],
        citations: [],
      };

      setMessages((prev) => [...prev, userMessage, assistantMessage]);

      try {
        const res = await fetch(`${FARMOS_API_BASE}/farm-agent/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ question: trimmed, session_id: sessionId }),
          signal: controller.signal,
        });

        if (!res.ok) {
          throw new Error(`Farm Agent 응답 실패 (${res.status})`);
        }

        await consumeSseStream(res, (event, data) => {
          if (event === 'session' && data) {
            setSessionId(data);
            return;
          }

          if (event === 'fast_path') {
            updateAssistant(assistantId, (m) => ({ ...m, fastPath: true }));
            return;
          }

          if (event === 'plan' && data) {
            updateAssistant(assistantId, (m) => ({
              ...m,
              steps: [...m.steps, { kind: 'plan', markdown: data }],
            }));
            return;
          }

          if (event === 'subagent' && data) {
            updateAssistant(assistantId, (m) => ({
              ...m,
              steps: [...m.steps, { kind: 'subagent', name: data }],
            }));
            return;
          }

          if (event === 'tool' && data) {
            updateAssistant(assistantId, (m) => {
              if (m.toolCalls.some((c) => c.name === data && !c.output)) return m;
              return {
                ...m,
                toolCalls: [...m.toolCalls, { toolCallId: '', name: data }],
              };
            });
            return;
          }

          if (event === 'tool_input') {
            const parsed = safeJson<{ tool_call_id: string; name: string; args: unknown }>(data);
            if (!parsed) return;
            updateAssistant(assistantId, (m) => {
              const idx = m.toolCalls.findIndex(
                (c) => c.name === parsed.name && !c.toolCallId,
              );
              if (idx >= 0) {
                const next = [...m.toolCalls];
                next[idx] = { ...next[idx], toolCallId: parsed.tool_call_id, args: parsed.args };
                return { ...m, toolCalls: next };
              }
              return {
                ...m,
                toolCalls: [
                  ...m.toolCalls,
                  { toolCallId: parsed.tool_call_id, name: parsed.name, args: parsed.args },
                ],
              };
            });
            return;
          }

          if (event === 'tool_output') {
            const parsed = safeJson<{ tool_call_id: string; name: string; result: string }>(data);
            if (!parsed) return;
            updateAssistant(assistantId, (m) => {
              const idx = m.toolCalls.findIndex(
                (c) =>
                  (parsed.tool_call_id && c.toolCallId === parsed.tool_call_id) ||
                  (c.name === parsed.name && !c.output),
              );
              if (idx >= 0) {
                const next = [...m.toolCalls];
                next[idx] = { ...next[idx], output: parsed.result };
                return { ...m, toolCalls: next };
              }
              return {
                ...m,
                toolCalls: [
                  ...m.toolCalls,
                  {
                    toolCallId: parsed.tool_call_id,
                    name: parsed.name,
                    output: parsed.result,
                  },
                ],
              };
            });
            return;
          }

          if (event === 'citations') {
            const parsed = safeJson<Citation[]>(data);
            if (!parsed) return;
            updateAssistant(assistantId, (m) => ({ ...m, citations: parsed }));
            return;
          }

          if (event === 'retry') {
            // Backend re-prompted because the first answer lacked the required
            // citation. The retry payload contains the FULL replacement content
            // — overwrite the bubble and clear the low_confidence chip since
            // the retry succeeded by definition (backend only emits this event
            // when the new answer carries citations).
            const parsed = safeJson<{ reason: string; content: string }>(data);
            if (!parsed?.content) return;
            updateAssistant(assistantId, (m) => ({
              ...m,
              content: parsed.content,
              lowConfidence: undefined,
            }));
            return;
          }

          if (event === 'low_confidence') {
            const parsed = safeJson<LowConfidence>(data);
            if (!parsed) return;
            updateAssistant(assistantId, (m) => ({ ...m, lowConfidence: parsed }));
            return;
          }

          if (event === 'action') {
            const parsed = safeJson<{ kind: string; control_type: string; summary: string }>(data);
            if (!parsed) return;
            updateAssistant(assistantId, (m) => ({
              ...m,
              action: {
                kind: 'iot_control',
                controlType: parsed.control_type,
                summary: parsed.summary,
                status: 'pending',
              },
            }));
            return;
          }

          if (event === 'token') {
            updateAssistant(assistantId, (m) => ({ ...m, content: m.content + data }));
            return;
          }

          if (event === 'error') {
            const serverMessage = data || 'Farm Agent 처리 중 오류가 발생했습니다.';
            setError(serverMessage);
            updateAssistant(assistantId, (m) => ({
              ...m,
              // Prefer the server's specific reason over the generic fallback
              // so users see "새 대화로 초기화하세요" instead of just "잠시 후
              // 다시 시도하세요". Only keep streamed content if there was any.
              content: m.content || serverMessage,
              error: true,
            }));
          }
        });

        updateAssistant(assistantId, (m) => ({ ...m, streaming: false }));
      } catch (err) {
        if (controller.signal.aborted) return;
        const message = err instanceof Error ? err.message : '질문을 전송할 수 없습니다.';
        setError(message);
        updateAssistant(assistantId, (m) => ({
          ...m,
          content: message,
          streaming: false,
          error: true,
        }));
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
        setBusyBoth(false);
        // 응답이 끝나면 사이드바 스레드 목록을 비동기 새로고침 — 신규 스레드 노출
        void fetchThreads();
      }
    },
    [sessionId, updateAssistant, fetchThreads, setBusyBoth],
  );

  const sendVoice = useCallback(
    async (audio: Blob) => {
      if (busyRef.current) return;
      const form = new FormData();
      form.append('file', audio, 'recording.webm');
      if (sessionId) form.append('session_id', sessionId);
      setBusyBoth(true);
      setError(null);
      try {
        const res = await fetch(`${FARMOS_API_BASE}/farm-agent/voice`, {
          method: 'POST',
          credentials: 'include',
          body: form,
        });
        if (!res.ok) throw new Error(`음성 처리 실패 (${res.status})`);
        const data = (await res.json()) as {
          transcript: string;
          answer: string;
          session_id: string;
          fast_path: boolean;
        };
        setSessionId(data.session_id);
        setMessages((prev) => [
          ...prev,
          {
            id: makeId('user'),
            role: 'user',
            content: data.transcript,
            steps: [],
            toolCalls: [],
            citations: [],
          },
          {
            id: makeId('agent'),
            role: 'assistant',
            content: data.answer,
            fastPath: data.fast_path,
            steps: [],
            toolCalls: [],
            citations: [],
          },
        ]);
      } catch (err) {
        const message = err instanceof Error ? err.message : '음성 전송 실패';
        setError(message);
      } finally {
        setBusyBoth(false);
      }
    },
    [sessionId, setBusyBoth],
  );

  const sendImage = useCallback(
    async (file: File, hint?: { crop?: string; region?: string }) => {
      if (busyRef.current) return;
      const form = new FormData();
      form.append('file', file);
      if (hint?.crop) form.append('crop', hint.crop);
      if (hint?.region) form.append('region', hint.region);
      setBusyBoth(true);
      setError(null);
      try {
        const res = await fetch(`${FARMOS_API_BASE}/farm-agent/diagnose-image`, {
          method: 'POST',
          credentials: 'include',
          body: form,
        });
        if (!res.ok) throw new Error(`이미지 진단 실패 (${res.status})`);
        const data = (await res.json()) as {
          pest: string | null;
          crop: string;
          region: string;
          image_url: string | null;
          answer: string;
          session_id: string;
        };
        setSessionId(data.session_id);
        setMessages((prev) => [
          ...prev,
          {
            id: makeId('user'),
            role: 'user',
            content: `이미지 진단 요청 — ${data.crop}, ${data.region}`,
            steps: [],
            toolCalls: [],
            citations: [],
            attachments: data.image_url
              ? [{ kind: 'image', url: data.image_url, alt: data.pest ?? '진단 이미지' }]
              : undefined,
          },
          {
            id: makeId('agent'),
            role: 'assistant',
            content: data.answer,
            steps: [],
            toolCalls: data.pest
              ? [{ toolCallId: '', name: 'classify_pest_image', output: `pest=${data.pest}` }]
              : [],
            citations: [],
          },
        ]);
      } catch (err) {
        const message = err instanceof Error ? err.message : '이미지 진단 실패';
        setError(message);
      } finally {
        setBusyBoth(false);
      }
    },
    [setBusyBoth],
  );

  // Read the message via setMessages-functional pattern instead of closing
  // over `messages` directly — `messages` mutates on every SSE token, which
  // would change this callback's identity each tick and force re-renders of
  // every consumer that memoizes on it (e.g. MessageBubble props).
  const messagesRef = useRef<FarmAgentMessage[]>([]);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const approveAction = useCallback(
    async (messageId: string, action: Record<string, unknown>) => {
      const message = messagesRef.current.find((m) => m.id === messageId);
      if (!message?.action || !sessionId) return;
      const controlType = message.action.controlType;
      updateAssistant(messageId, (m) => ({
        ...m,
        action: m.action ? { ...m.action, status: 'executing' } : m.action,
      }));
      try {
        const res = await fetch(`${FARMOS_API_BASE}/farm-agent/approve-action`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          credentials: 'include',
          body: JSON.stringify({
            session_id: sessionId,
            control_type: controlType,
            action,
          }),
        });
        if (!res.ok) {
          // Some error paths return text/HTML — surface the status so the user
          // sees something actionable instead of a JSON-parse exception.
          const text = await res.text().catch(() => '');
          throw new Error(text || `${res.status} ${res.statusText || '실행 실패'}`);
        }
        const data = (await res.json()) as { ok: boolean; detail?: string };
        updateAssistant(messageId, (m) => ({
          ...m,
          action: m.action
            ? {
                ...m.action,
                status: data.ok ? 'approved' : 'failed',
                detail: data.detail,
              }
            : m.action,
        }));
      } catch (err) {
        const detail = err instanceof Error ? err.message : '실행 실패';
        updateAssistant(messageId, (m) => ({
          ...m,
          action: m.action ? { ...m.action, status: 'failed', detail } : m.action,
        }));
      }
    },
    [sessionId, updateAssistant],
  );

  const rejectAction = useCallback(
    (messageId: string) => {
      updateAssistant(messageId, (m) => ({
        ...m,
        action: m.action ? { ...m.action, status: 'rejected' } : m.action,
      }));
    },
    [updateAssistant],
  );

  // 초기 로드: 스레드 목록은 마운트 시 1회 가져온다 (드로어가 닫힌 상태여도 캐시).
  useEffect(() => {
    void fetchThreads();
  }, [fetchThreads]);

  return {
    messages,
    sessionId,
    briefing,
    briefingLoading,
    busy,
    error,
    threads,
    threadsLoading,
    send,
    sendVoice,
    sendImage,
    approveAction,
    rejectAction,
    stop,
    reset,
    fetchBriefing,
    fetchThreads,
    loadThread,
  };
}
