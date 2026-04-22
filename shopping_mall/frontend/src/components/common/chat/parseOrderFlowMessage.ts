/**
 * OrderGraph 플로우 메시지 파서
 *
 * 백엔드 order_graph/prompts.py 의 ORDER_PROMPTS 렌더링 결과를 분석해
 * UI 렌더링 타입(OrderFlowUI)을 결정합니다.
 *
 * ⚠️  각 분기의 앵커 문자열은 ORDER_PROMPTS 의 고정 리터럴과 1:1 대응합니다.
 *     prompts.py 를 수정할 때는 반드시 parseOrderFlowMessage.test.ts 도 확인하세요.
 */

export interface OrderSelectItem {
  num: string;
  orderId: string;
  summary: string;
  date: string;
}

export interface SimpleOption {
  num: string;
  label: string;
  isOther: boolean;
}

export interface ItemOption {
  num: string;
  label: string;
}

export type OrderFlowUI =
  | { type: 'confirm' }
  | { type: 'order-select'; items: OrderSelectItem[] }
  | { type: 'simple-options'; items: SimpleOption[] }
  | { type: 'item-select'; items: ItemOption[] }
  | null;

/**
 * 봇 메시지 텍스트를 분석해 인터랙티브 UI 타입을 반환합니다.
 * 일치하는 패턴이 없으면 null 을 반환합니다.
 *
 * 분기별 앵커 (ORDER_PROMPTS 리터럴 발췌):
 *   confirm       → "(네 / 아니오)"
 *   order-select  → "번호(1, 2" + "를 입력해 주세요"
 *   item-select   → "교환할 상품과 수량을 알려주세요"
 *   simple-options→ "번호 또는 사유를 입력해 주세요" | "번호를 입력해 주세요"
 */
export function parseOrderFlowMessage(text: string): OrderFlowUI {
  if (!text) return null;

  // 1. 최종 확인: (네 / 아니오)
  //    prompts: cancel_summary, exchange_summary, stock_insufficient
  if (text.includes('(네 / 아니오)')) {
    return { type: 'confirm' };
  }

  // 2. 주문 선택: "번호(1, 2 …)를 입력해 주세요" 패턴
  //    prompts: select_order_cancel, select_order_exchange, invalid_order_selection
  if (text.includes('번호(1, 2') && text.includes('를 입력해 주세요')) {
    const items: OrderSelectItem[] = [];
    const lines = text.split('\n');
    let current: Partial<OrderSelectItem> | null = null;

    for (const line of lines) {
      const numMatch = line.match(/^(\d+)\) 주문 번호 (#\d+)/);
      if (numMatch) {
        if (current?.num) items.push(current as OrderSelectItem);
        current = { num: numMatch[1], orderId: numMatch[2], summary: '', date: '' };
      } else if (current) {
        const productMatch = line.match(/· 상품: (.+)/);
        const dateMatch = line.match(/· 주문일: (.+)/);
        if (productMatch) current.summary = productMatch[1].trim();
        if (dateMatch) current.date = dateMatch[1].trim();
      }
    }
    if (current?.num) items.push(current as OrderSelectItem);
    if (items.length > 0) return { type: 'order-select', items };
  }

  // 3. 교환 품목 선택
  //    prompts: select_items
  if (text.includes('교환할 상품과 수량을 알려주세요')) {
    const items: ItemOption[] = [];
    for (const line of text.split('\n')) {
      const m = line.match(/^(\d+)\.\s+(.+)/);
      if (m) items.push({ num: m[1], label: m[2].trim() });
    }
    return { type: 'item-select', items };
  }

  // 4. 사유 선택 / 환불 방법 선택
  //    prompts: cancel_reason, exchange_reason  → "번호 또는 사유를 입력해 주세요"
  //             refund_method                   → "번호를 입력해 주세요"
  if (
    text.includes('번호 또는 사유를 입력해 주세요') ||
    text.includes('번호를 입력해 주세요')
  ) {
    const items: SimpleOption[] = [];
    for (const line of text.split('\n')) {
      const m = line.match(/^(\d+)\.\s+(.+)/);
      if (m) {
        const label = m[2].trim();
        items.push({ num: m[1], label, isOther: label.startsWith('기타') });
      }
    }
    if (items.length > 0) return { type: 'simple-options', items };
  }

  return null;
}
