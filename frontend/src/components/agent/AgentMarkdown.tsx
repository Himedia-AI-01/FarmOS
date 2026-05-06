import { useEffect, useMemo, useRef, useState } from 'react';
import { MdClose, MdOpenInNew } from 'react-icons/md';
import type { Citation } from '@/hooks/useFarmAgent';

// 가벼운 마크다운 렌더러 — react-markdown 의존 없이 농민 도메인에 필요한
// 굵게/리스트/제목/인용/[doc > 제N조] 인용 칩만 지원.
//
// 인용 칩(span)은 클릭 가능한 버튼으로 변경 — 클릭 시 CitationModal 이
// 시행지침 원문 전체와 doc 메타데이터를 보여준다 (이전엔 title 툴팁만).

function renderInline(
  text: string,
  citations: Citation[],
  onOpen: (c: Citation | { label: string }) => void,
) {
  const citationRegex = /\[([^[\]]+?>\s*제\s*\d+\s*조[^[\]]*?)\]/g;
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
        <button
          key={`cite-${idx}`}
          type="button"
          onClick={() => onOpen(cite ?? { label: tok.value })}
          title={cite?.snippet ? '클릭해 시행지침 원문 보기' : tok.value}
          className="cite-chip cursor-pointer hover:brightness-110 transition"
        >
          §{tok.value}
        </button>,
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
  const [activeCite, setActiveCite] = useState<Citation | { label: string } | null>(null);

  const open = (c: Citation | { label: string }) => setActiveCite(c);
  return (
    <div className="prose-farm">
      {lines.map((line, index) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={`space-${index}`} aria-hidden className="h-1" />;
        if (trimmed.startsWith('### ')) {
          return (
            <h3 key={index}>
              {renderInline(trimmed.replace(/^###\s+/, ''), citations, open)}
            </h3>
          );
        }
        if (trimmed.startsWith('## ')) {
          return (
            <h2 key={index}>
              {renderInline(trimmed.replace(/^##\s+/, ''), citations, open)}
            </h2>
          );
        }
        if (/^[-*]\s+/.test(trimmed)) {
          return (
            <div key={index} className="flex gap-2.5">
              <span className="mt-[0.65rem] h-1.5 w-1.5 flex-shrink-0 rounded-full bg-[color:var(--color-primary)]" aria-hidden />
              <p className="min-w-0 m-0">
                {renderInline(trimmed.replace(/^[-*]\s+/, ''), citations, open)}
              </p>
            </div>
          );
        }
        if (trimmed.startsWith('> ')) {
          return (
            <blockquote key={index}>
              {renderInline(trimmed.slice(2), citations, open)}
            </blockquote>
          );
        }
        return (
          <p key={index} className="m-0">
            {renderInline(trimmed, citations, open)}
          </p>
        );
      })}
      {activeCite && <CitationModal citation={activeCite} onClose={() => setActiveCite(null)} />}
    </div>
  );
}

function CitationModal({
  citation,
  onClose,
}: {
  citation: Citation | { label: string };
  onClose: () => void;
}) {
  const full = 'snippet' in citation ? citation : null;
  const label = 'label' in citation ? citation.label : '';
  const [doc, clause] = label.split('>').map((s) => s.trim());
  const headingId = useRef(`citation-modal-heading-${Math.random().toString(36).slice(2, 8)}`).current;
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);

  // Keyboard a11y: ESC closes (the footer text already promises this), and
  // initial focus moves to the close button so screen readers and keyboard
  // users land somewhere sensible instead of on the underlying page.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    closeBtnRef.current?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby={headingId}
    >
      <div
        className="max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-2xl bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-bold uppercase tracking-wide text-[color:var(--color-ink-mute)]">
              근거 조항
            </p>
            <h3 id={headingId} className="mt-1 text-lg font-bold text-[color:var(--color-ink)]">
              {clause || label || '(미상)'}
            </h3>
            {doc && doc !== clause && (
              <p className="mt-0.5 text-xs text-[color:var(--color-ink-mute)]">{doc}</p>
            )}
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full text-[color:var(--color-ink-faint)] hover:bg-[color:var(--color-surface)] hover:text-[color:var(--color-ink-soft)] transition"
            aria-label="닫기"
          >
            <MdClose className="text-xl" />
          </button>
        </div>

        {full ? (
          <>
            <div className="rounded-xl border border-[color:var(--color-line)] bg-[color:var(--color-surface)] p-4">
              <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-[color:var(--color-ink-soft)]">
                {full.snippet}
              </p>
            </div>
            {(() => {
              const sim = (full as unknown as { similarity?: number }).similarity;
              return typeof sim === 'number' ? (
                <p className="mt-2 text-[11px] text-[color:var(--color-ink-faint)]">
                  검색 유사도 {(sim * 100).toFixed(1)}%
                </p>
              ) : null;
            })()}
          </>
        ) : (
          <div className="rounded-xl border border-dashed border-[color:var(--color-line)] bg-[color:var(--color-surface)] p-4 text-sm text-[color:var(--color-ink-mute)]">
            이 인용에 해당하는 시행지침 원문 발췌가 검색되지 않았습니다. 답변에 인용된 조항이 실제 문서에 존재하는지 확인이 필요합니다.
          </div>
        )}

        <div className="mt-4 flex items-center justify-between text-xs text-[color:var(--color-ink-faint)]">
          <span className="flex items-center gap-1">
            <MdOpenInNew className="text-sm" />
            출처: 농림축산식품부 시행지침
          </span>
          <span>ESC 또는 바깥 클릭으로 닫기</span>
        </div>
      </div>
    </div>
  );
}
