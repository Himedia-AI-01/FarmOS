import { useMemo } from 'react';
import type { Citation } from '@/hooks/useFarmAgent';

// 가벼운 마크다운 렌더러 — react-markdown 의존 없이 농민 도메인에 필요한
// 굵게/리스트/제목/인용/[doc > 제N조] 인용 칩만 지원.

function renderInline(text: string, citations: Citation[]) {
  const citationRegex = /\[([^\[\]]+?>\s*제\s*\d+\s*조[^\[\]]*?)\]/g;
  const tokens: Array<{ type: 'text' | 'citation'; value: string }> = [];

  let cursor = 0;
  for (const match of text.matchAll(citationRegex)) {
    if (match.index == null) continue;
    if (match.index > cursor) {
      tokens.push({ type: 'text', value: text.slice(cursor, match.index) });
    }
    tokens.push({ type: 'citation', value: match[1].trim() });
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) tokens.push({ type: 'text', value: text.slice(cursor) });

  const out: React.ReactNode[] = [];
  tokens.forEach((tok, idx) => {
    if (tok.type === 'citation') {
      const cite = citations.find((c) => c.label === tok.value);
      out.push(
        <span
          key={`cite-${idx}`}
          title={cite?.snippet || tok.value}
          className="cite-chip"
        >
          §{tok.value}
        </span>,
      );
      return;
    }
    const parts = tok.value.split(/(\*\*[^*]+\*\*)/g);
    parts.forEach((part, j) => {
      if (part.startsWith('**') && part.endsWith('**')) {
        out.push(<strong key={`b-${idx}-${j}`}>{part.slice(2, -2)}</strong>);
      } else {
        out.push(<span key={`t-${idx}-${j}`}>{part}</span>);
      }
    });
  });
  return out;
}

export function AgentMarkdown({
  content,
  citations = [],
}: {
  content: string;
  citations?: Citation[];
}) {
  const lines = useMemo(() => content.split(/\r?\n/), [content]);
  return (
    <div className="prose-farm">
      {lines.map((line, index) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={`space-${index}`} aria-hidden className="h-1" />;
        if (trimmed.startsWith('### ')) {
          return (
            <h3 key={index}>
              {renderInline(trimmed.replace(/^###\s+/, ''), citations)}
            </h3>
          );
        }
        if (trimmed.startsWith('## ')) {
          return (
            <h2 key={index}>
              {renderInline(trimmed.replace(/^##\s+/, ''), citations)}
            </h2>
          );
        }
        if (/^[-*]\s+/.test(trimmed)) {
          return (
            <div key={index} className="flex gap-2.5">
              <span className="mt-[0.65rem] h-1.5 w-1.5 flex-shrink-0 rounded-full bg-[color:var(--color-primary)]" aria-hidden />
              <p className="min-w-0 m-0">
                {renderInline(trimmed.replace(/^[-*]\s+/, ''), citations)}
              </p>
            </div>
          );
        }
        if (trimmed.startsWith('> ')) {
          return (
            <blockquote key={index}>
              {renderInline(trimmed.slice(2), citations)}
            </blockquote>
          );
        }
        return (
          <p key={index} className="m-0">
            {renderInline(trimmed, citations)}
          </p>
        );
      })}
    </div>
  );
}
