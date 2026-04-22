import React from 'react';

/**
 * Bot 응답 텍스트에서 주문번호 패턴(#\d+)을 감지하여
 * /mypage/orders/:id 로 연결되는 새 탭 링크로 변환합니다.
 *
 * 예) "주문번호: #42" → "주문번호: " + <a href="/mypage/orders/42" target="_blank">#42</a>
 */
export function renderChatText(text: string): React.ReactNode {
  if (!text) return null;
  const parts = text.split(/(#\d+)/g);
  if (parts.length === 1) return text;

  return (
    <>
      {parts.map((part, i) => {
        const m = /^#(\d+)$/.exec(part);
        if (m) {
          const prev = i > 0 ? parts[i - 1] : '';
          const hasOrderContext = /주문(?:\s*번호)?[\s:：(]*$/.test(prev);
          if (hasOrderContext) {
            return (
              <a
                key={i}
                href={`/mypage/orders/${m[1]}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[#03C75A] underline font-medium hover:opacity-80"
                onClick={(e) => e.stopPropagation()}
              >
                {part}
              </a>
            );
          }
        }
        return <React.Fragment key={i}>{part}</React.Fragment>;
      })}
    </>
  );
}
