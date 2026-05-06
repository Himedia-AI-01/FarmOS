import { useEffect } from 'react';

// Lock body scroll while `active`. Preserves the user's scroll position and
// compensates for the scrollbar gutter so the layout doesn't shift.
export function useScrollLock(active: boolean) {
  useEffect(() => {
    if (!active) return;
    const { body, documentElement } = document;
    const scrollbarGutter = window.innerWidth - documentElement.clientWidth;
    const prevOverflow = body.style.overflow;
    const prevPaddingRight = body.style.paddingRight;
    body.style.overflow = 'hidden';
    if (scrollbarGutter > 0) {
      body.style.paddingRight = `${scrollbarGutter}px`;
    }
    return () => {
      body.style.overflow = prevOverflow;
      body.style.paddingRight = prevPaddingRight;
    };
  }, [active]);
}
