import { useCallback, useRef, useState } from 'react';
import { FARMOS_API_BASE } from '@/lib/api';

export interface FarmAgentMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  streaming?: boolean;
  fastPath?: boolean;
  tools?: string[];
  error?: boolean;
}

export interface FarmAgentBriefing {
  date: string;
  content: string;
  cached: boolean;
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

export function useFarmAgent() {
  const [messages, setMessages] = useState<FarmAgentMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [briefing, setBriefing] = useState<FarmAgentBriefing | null>(null);
  const [briefingLoading, setBriefingLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

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

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
    setMessages((prev) =>
      prev.map((message) =>
        message.streaming ? { ...message, streaming: false } : message,
      ),
    );
  }, []);

  const reset = useCallback(() => {
    stop();
    setMessages([]);
    setSessionId(null);
    setError(null);
  }, [stop]);

  const send = useCallback(
    async (question: string) => {
      const trimmed = question.trim();
      if (!trimmed || busy) return;

      const controller = new AbortController();
      abortRef.current = controller;
      setBusy(true);
      setError(null);

      const userMessage: FarmAgentMessage = {
        id: makeId('user'),
        role: 'user',
        content: trimmed,
      };
      const assistantId = makeId('agent');
      const assistantMessage: FarmAgentMessage = {
        id: assistantId,
        role: 'assistant',
        content: '',
        streaming: true,
        tools: [],
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
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantId ? { ...message, fastPath: true } : message,
              ),
            );
            return;
          }

          if (event === 'tool' && data) {
            setMessages((prev) =>
              prev.map((message) => {
                if (message.id !== assistantId) return message;
                const tools = message.tools ?? [];
                return { ...message, tools: tools.includes(data) ? tools : [...tools, data] };
              }),
            );
            return;
          }

          if (event === 'token') {
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantId
                  ? { ...message, content: message.content + data }
                  : message,
              ),
            );
            return;
          }

          if (event === 'error') {
            setError(data || 'Farm Agent 처리 중 오류가 발생했습니다.');
            setMessages((prev) =>
              prev.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      content:
                        message.content ||
                        '답변을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요.',
                      error: true,
                    }
                  : message,
              ),
            );
          }
        });

        setMessages((prev) =>
          prev.map((message) =>
            message.id === assistantId ? { ...message, streaming: false } : message,
          ),
        );
      } catch (err) {
        if (controller.signal.aborted) return;
        const message = err instanceof Error ? err.message : '질문을 전송할 수 없습니다.';
        setError(message);
        setMessages((prev) =>
          prev.map((item) =>
            item.id === assistantId
              ? {
                  ...item,
                  content: message,
                  streaming: false,
                  error: true,
                }
              : item,
          ),
        );
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
        }
        setBusy(false);
      }
    },
    [busy, sessionId],
  );

  return {
    messages,
    sessionId,
    briefing,
    briefingLoading,
    busy,
    error,
    send,
    stop,
    reset,
    fetchBriefing,
  };
}
