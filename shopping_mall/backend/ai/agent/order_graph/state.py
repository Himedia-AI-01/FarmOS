"""OrderGraph 상태 스키마."""
from typing import Literal, NotRequired, Required, TypedDict

OrderAction = Literal["cancel", "exchange", "change"]


class OrderState(TypedDict):
    # ── 컨텍스트 (최초 ainvoke 시 주입) ────────────────────────────────────
    action: Required[OrderAction]
    user_id: Required[int]
    session_id: Required[int]
    user_message: Required[str]     # interrupt resume 시 사용자 입력값

    # ── 단계별 수집 데이터 ────────────────────────────────────────────────
    order_id: NotRequired[int | None]
    order_display: NotRequired[str | None]   # "주문 #12 - 딸기 2kg (2026-04-18)" 형식 (사용자 표시용)
    selected_items: NotRequired[list]        # [{"item_id": int, "name": str, "qty": int}]
    reason: NotRequired[str | None]          # 교환/취소 사유
    refund_method: NotRequired[str | None]   # 취소 시 환불 방법
    change_type: NotRequired[str | None]     # 주문 변경 유형
    change_detail: NotRequired[str | None]   # 주문 변경 요청 상세
    stock_note: NotRequired[str]             # 교환 품목 재고 부족 안내 (check_stock → show_summary 전달용)

    # ── 흐름 제어 ─────────────────────────────────────────────────────────
    confirmed: NotRequired[bool | None]  # None=미결, True=최종 승인, False=거부
    abort: NotRequired[bool]             # True → 즉시 handle_flow_cancel로 라우팅
    confirmation_attempts: NotRequired[int]  # show_summary 재진입 횟수 — 3회 초과 시 강제 탈출

    # ── 출력 ──────────────────────────────────────────────────────────────
    ticket_id: NotRequired[int | None]
    response: NotRequired[str]    # 완료 노드가 설정하는 최종 메시지 (Supervisor에게 반환)
    is_pending: NotRequired[bool]  # True: 사용자 입력 대기 / False: 최종 완료


def initial_order_state(
    *,
    action: OrderAction,
    user_id: int,
    session_id: int,
    user_message: str,
) -> OrderState:
    """OrderGraph 신규 플로우용 기본 상태를 생성한다."""
    return {
        "action": action,
        "user_id": user_id,
        "session_id": session_id,
        "user_message": user_message,
        "order_id": None,
        "order_display": None,
        "selected_items": [],
        "reason": None,
        "refund_method": None,
        "change_type": None,
        "change_detail": None,
        "stock_note": "",
        "confirmed": None,
        "abort": False,
        "confirmation_attempts": 0,
        "ticket_id": None,
        "response": "",
        "is_pending": True,
    }
