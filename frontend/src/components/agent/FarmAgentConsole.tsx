import { useEffect, useMemo, useRef, useState } from 'react';
import {
  MdAutoAwesome,
  MdClose,
  MdOutlineArticle,
  MdRefresh,
  MdRestartAlt,
  MdSend,
  MdStopCircle,
  MdTaskAlt,
} from 'react-icons/md';
import { useFarmAgent, type FarmAgentMessage } from '@/hooks/useFarmAgent';

interface FarmAgentConsoleProps {
  surface?: 'rail' | 'drawer';
  onClose?: () => void;
}

const STARTER_PROMPTS = [
  '오늘 해야 할 작업을 우선순위로 정리해줘',
  '현재 센서 상태에서 관수가 필요한지 판단해줘',
  '내 농장 기준 공익직불 신청 리스크를 확인해줘',
  '최근 시세와 날씨를 보고 출하 타이밍을 봐줘',
];

function renderInline(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <strong key={`${part}-${index}`} className="font-semibold text-gray-950">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return part;
  });
}

function AgentText({ content }: { content: string }) {
  const lines = useMemo(() => content.split(/\r?\n/), [content]);

  return (
    <div className="space-y-2 text-sm leading-relaxed">
      {lines.map((line, index) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={`space-${index}`} className="h-1" />;
        if (trimmed.startsWith('### ')) {
          return (
            <h4 key={`${trimmed}-${index}`} className="pt-1 text-sm font-bold text-gray-950">
              {renderInline(trimmed.replace(/^###\s+/, ''))}
            </h4>
          );
        }
        if (trimmed.startsWith('## ')) {
          return (
            <h3 key={`${trimmed}-${index}`} className="pt-2 text-base font-bold text-gray-950">
              {renderInline(trimmed.replace(/^##\s+/, ''))}
            </h3>
          );
        }
        if (/^[-*]\s+/.test(trimmed)) {
          return (
            <div key={`${trimmed}-${index}`} className="flex gap-2">
              <span className="mt-[0.45rem] h-1.5 w-1.5 flex-shrink-0 rounded-full bg-primary" />
              <p className="min-w-0 text-gray-700">{renderInline(trimmed.replace(/^[-*]\s+/, ''))}</p>
            </div>
          );
        }
        return (
          <p key={`${trimmed}-${index}`} className="text-gray-700">
            {renderInline(trimmed)}
          </p>
        );
      })}
    </div>
  );
}

function ToolBadge({ tool }: { tool: string }) {
  const readable = tool
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());

  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-cyan-200 bg-cyan-50 px-2 py-1 text-[11px] font-semibold text-cyan-800">
      <MdTaskAlt className="text-sm" />
      {readable}
    </span>
  );
}

function MessageBubble({ message }: { message: FarmAgentMessage }) {
  const isUser = message.role === 'user';

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[92%] rounded-lg border px-3 py-2.5 shadow-sm ${
          isUser
            ? 'border-primary bg-primary text-white'
            : message.error
              ? 'border-red-200 bg-red-50'
              : 'border-gray-200 bg-white'
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-white">{message.content}</p>
        ) : (
          <div className="space-y-2">
            {message.tools && message.tools.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {message.tools.map((tool) => (
                  <ToolBadge key={tool} tool={tool} />
                ))}
              </div>
            )}
            {message.content ? (
              <AgentText content={message.content} />
            ) : message.streaming ? (
              <div className="flex items-center gap-1.5 py-1" aria-live="polite">
                <span className="h-2 w-2 rounded-full bg-primary/70 animate-pulse" />
                <span className="h-2 w-2 rounded-full bg-primary/50 animate-pulse [animation-delay:120ms]" />
                <span className="h-2 w-2 rounded-full bg-primary/30 animate-pulse [animation-delay:240ms]" />
              </div>
            ) : (
              <p className="text-sm text-gray-500">
                응답을 표시하지 못했습니다. 다시 시도해 주세요.
              </p>
            )}
            {message.streaming && message.content && (
              <span className="inline-block h-4 w-[2px] bg-primary align-middle animate-pulse" />
            )}
            {message.fastPath && (
              <span className="inline-flex rounded-md bg-emerald-50 px-2 py-1 text-[11px] font-semibold text-emerald-700">
                Fast path
              </span>
            )}
          </div>
        )}
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
    send,
    stop,
    reset,
    fetchBriefing,
  } = useFarmAgent();
  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    void fetchBriefing(false);
  }, [fetchBriefing]);

  const lastMessageLength = messages[messages.length - 1]?.content.length ?? 0;
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [messages.length, lastMessageLength]);

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

  return (
    <section
      className={`flex h-full min-h-0 flex-col bg-white ${
        surface === 'drawer' ? 'rounded-l-lg shadow-2xl' : ''
      }`}
      aria-label="Farm Agent"
    >
      <div className="flex items-center gap-3 border-b border-gray-200 px-4 py-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-white">
          <MdAutoAwesome className="text-xl" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-base font-bold text-gray-950">Farm Agent</h2>
            <span
              className={`h-2 w-2 rounded-full ${
                busy ? 'bg-cyan-500 animate-pulse' : 'bg-emerald-500'
              }`}
              aria-hidden
            />
          </div>
          <p className="truncate text-xs text-gray-500">
            {sessionId ? `thread ${sessionId.slice(0, 8)}` : 'ready'}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void fetchBriefing(true)}
          className="flex h-9 w-9 items-center justify-center rounded-md text-gray-500 transition hover:bg-gray-100 hover:text-gray-900"
          title="브리핑 새로고침"
          aria-label="브리핑 새로고침"
        >
          <MdRefresh className={`text-xl ${briefingLoading ? 'animate-spin' : ''}`} />
        </button>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            className="flex h-9 w-9 items-center justify-center rounded-md text-gray-500 transition hover:bg-gray-100 hover:text-gray-900"
            aria-label="닫기"
          >
            <MdClose className="text-xl" />
          </button>
        )}
      </div>

      <div className="border-b border-gray-200 px-4 py-3">
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-bold text-gray-900">
            <MdOutlineArticle className="text-primary" />
            오늘 브리핑
          </div>
          {briefing?.cached && (
            <span className="rounded-md bg-gray-100 px-2 py-0.5 text-[11px] font-semibold text-gray-500">
              cached
            </span>
          )}
        </div>
        <div className="max-h-44 overflow-y-auto rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
          {briefingLoading && !briefing ? (
            <p className="text-sm text-gray-400">브리핑 생성 중...</p>
          ) : briefing ? (
            <AgentText content={briefing.content} />
          ) : (
            <p className="text-sm text-gray-400">브리핑을 불러오지 못했습니다.</p>
          )}
        </div>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <div className="space-y-2">
            {STARTER_PROMPTS.map((prompt) => (
              <button
                key={prompt}
                type="button"
                onClick={() => sendInput(prompt)}
                className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-left text-sm font-medium text-gray-700 transition hover:border-primary/40 hover:bg-primary/5 hover:text-primary"
              >
                {prompt}
              </button>
            ))}
          </div>
        ) : (
          messages.map((message) => <MessageBubble key={message.id} message={message} />)
        )}
        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}
      </div>

      <form onSubmit={submit} className="border-t border-gray-200 p-3">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                sendInput(input);
              }
            }}
            placeholder="질문 입력"
            className="max-h-32 min-h-11 flex-1 resize-none rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/15"
            disabled={busy}
          />
          <button
            type="submit"
            disabled={!input.trim() || busy}
            className="flex h-11 w-11 items-center justify-center rounded-lg bg-primary text-white transition hover:bg-primary-dark disabled:cursor-not-allowed disabled:bg-gray-300"
            aria-label="전송"
          >
            <MdSend className="text-xl" />
          </button>
        </div>
        <div className="mt-2 flex items-center justify-between">
          <button
            type="button"
            onClick={reset}
            disabled={busy && messages.length === 0}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold text-gray-500 transition hover:bg-gray-100 hover:text-gray-900 disabled:opacity-40"
          >
            <MdRestartAlt className="text-base" />
            새 대화
          </button>
          {busy && (
            <button
              type="button"
              onClick={stop}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold text-red-600 transition hover:bg-red-50"
            >
              <MdStopCircle className="text-base" />
              중지
            </button>
          )}
        </div>
      </form>
    </section>
  );
}
