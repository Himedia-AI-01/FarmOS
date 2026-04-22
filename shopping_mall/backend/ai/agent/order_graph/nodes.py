"""OrderGraph 노드 함수들.

각 노드는 config["configurable"]["db"]로 DB Session을 주입받습니다.
interrupt()를 호출하는 노드는 재실행 시 DB 조회를 다시 수행합니다 (읽기 전용이므로 안전).
"""
import json
import logging
import re
from datetime import datetime, timezone

from langgraph.types import interrupt, RunnableConfig

from .state import OrderState
from .prompts import (
    ORDER_PROMPTS,
    CANCEL_KEYWORDS,
    CONFIRM_KEYWORDS,
    CANCEL_REASON_MAP,
    EXCHANGE_REASON_MAP,
    REFUND_METHOD_MAP,
)

logger = logging.getLogger(__name__)


# ── 상수 ───────────────────────────────────────────────────────────────────────

# 취소 가능 상태: 아직 배송사에 픽업되지 않은 주문
CANCELLABLE_STATUSES: frozenset[str] = frozenset({"pending", "registered"})
# 교환 가능 상태: 수령 완료된 주문
EXCHANGEABLE_STATUSES: frozenset[str] = frozenset({"delivered"})

_STATUS_DISPLAY: dict[str, str] = {
    "pending":    "결제 완료 (배송 준비 전)",
    "registered": "배송 준비 중",
    "picked_up":  "배송 중 (픽업 완료)",
    "in_transit": "배송 중",
    "delivered":  "배송 완료",
    "cancelled":  "취소 완료",
}


# ── 헬퍼 함수 ──────────────────────────────────────────────────────────────────

def _get_db(config: RunnableConfig):
    return config["configurable"]["db"]


def _is_cancel_intent(text: str) -> bool:
    text_lower = text.strip().lower()
    return any(kw in text_lower for kw in CANCEL_KEYWORDS)


def _is_confirm_intent(text: str) -> bool:
    text_lower = text.strip().lower()
    return any(kw in text_lower for kw in CONFIRM_KEYWORDS)


def _format_order_summary(db, order) -> str:
    """주문 요약 문자열 생성 (주문 품목 첫 번째 상품명 포함)."""
    from app.models.order import OrderItem
    from app.models.product import Product

    first_item = (
        db.query(OrderItem)
        .filter(OrderItem.order_id == order.id)
        .first()
    )
    if first_item:
        product = db.query(Product).filter(Product.id == first_item.product_id).first()
        product_name = product.name if product else "상품"
        item_count = db.query(OrderItem).filter(OrderItem.order_id == order.id).count()
        suffix = f" 외 {item_count - 1}건" if item_count > 1 else ""
        return f"{product_name}{suffix}"
    return "주문 상품"


def _parse_order_selection(text: str, orders: list) -> int | None:
    """사용자 입력에서 주문 ID 파싱. 번호(1~N) 또는 주문 ID 직접 입력."""
    text = text.strip()
    # 숫자만 추출
    nums = re.findall(r"\d+", text)
    if not nums:
        return None
    n = int(nums[0])
    # 1~N 범위 번호인지 확인
    if 1 <= n <= len(orders):
        return orders[n - 1].id
    # 직접 주문 ID인지 확인
    for order in orders:
        if order.id == n:
            return order.id
    return None


_MAX_REASON_LENGTH = 200


def _parse_reason(text: str, reason_map: dict[str, str]) -> str:
    """번호 또는 직접 입력 사유 파싱."""
    text = text.strip()
    if text in reason_map:
        return reason_map[text]
    # 앞 숫자 추출 시도
    m = re.match(r"^(\d+)", text)
    if m and m.group(1) in reason_map:
        return reason_map[m.group(1)]
    # 그대로 사유로 사용 (직접 입력) — 길이 제한 적용
    return text[:_MAX_REASON_LENGTH] if text else "기타"


def _parse_refund_method(text: str) -> str:
    text = text.strip()
    if text in REFUND_METHOD_MAP:
        return REFUND_METHOD_MAP[text]
    if "1" in text or "원결제" in text or "카드" in text:
        return REFUND_METHOD_MAP["1"]
    if "2" in text or "적립" in text or "포인트" in text:
        return REFUND_METHOD_MAP["2"]
    return REFUND_METHOD_MAP["1"]  # 기본값


# ── 노드 함수 ─────────────────────────────────────────────────────────────────

async def route_action(state: OrderState, config: RunnableConfig) -> dict:
    """취소/교환 분기 라우팅용 passthrough 노드."""
    return state


async def list_orders(state: OrderState, config: RunnableConfig) -> dict:
    """취소/교환 가능한 주문 목록 조회 → interrupt로 선택 대기.

    - 취소: pending / registered 상태만 (배송 픽업 전)
    - 교환: delivered 상태만 (수령 완료)
    """
    from app.models.order import Order

    db = _get_db(config)

    eligible_statuses = (
        CANCELLABLE_STATUSES if state["action"] == "cancel" else EXCHANGEABLE_STATUSES
    )
    no_orders_key = (
        "no_cancellable_orders" if state["action"] == "cancel" else "no_exchangeable_orders"
    )

    orders = (
        db.query(Order)
        .filter(
            Order.user_id == state["user_id"],
            Order.status.in_(eligible_statuses),
        )
        .order_by(Order.created_at.desc())
        .limit(5)
        .all()
    )

    if not orders:
        return {
            **state,
            "abort": True,
            "response": ORDER_PROMPTS[no_orders_key],
            "is_pending": False,
        }

    order_lines = []
    for i, o in enumerate(orders):
        summary = _format_order_summary(db, o)
        date_str = o.created_at.strftime("%Y-%m-%d")
        status_display = _STATUS_DISPLAY.get(o.status, o.status)
        order_lines.append(
            f"{i + 1}) 주문 번호 #{o.id}\n"
            f"   · 상품: {summary}\n"
            f"   · 주문일: {date_str}\n"
            f"   · 상태: {status_display}"
        )
    order_list = "\n\n".join(order_lines)

    prompt_key = "select_order_cancel" if state["action"] == "cancel" else "select_order_exchange"
    prompt = ORDER_PROMPTS[prompt_key].format(order_list=order_list)

    # ── interrupt: 사용자 주문 선택 대기 ──────────────────────────────────
    user_input = interrupt(prompt)

    if _is_cancel_intent(user_input):
        return {**state, "abort": True, "response": ORDER_PROMPTS["flow_cancelled"], "is_pending": False}

    # 단일 주문일 때 긍정 응답("응", "네", "진행해줘" 등) → 유일한 주문 자동 선택
    if len(orders) == 1 and _is_confirm_intent(user_input):
        order_id = orders[0].id
    else:
        order_id = _parse_order_selection(user_input, orders)

    if order_id is None:
        # 한 번 더 물어보기
        retry_prompt = ORDER_PROMPTS["invalid_order_selection"].format(order_list=order_list)
        user_input = interrupt(retry_prompt)
        if _is_cancel_intent(user_input):
            return {**state, "abort": True, "response": ORDER_PROMPTS["flow_cancelled"], "is_pending": False}
        if len(orders) == 1 and _is_confirm_intent(user_input):
            order_id = orders[0].id
        else:
            order_id = _parse_order_selection(user_input, orders)

    if order_id is None:
        return {
            **state,
            "abort": True,
            "response": "주문을 확인하지 못했습니다. 처음부터 다시 시도해 주세요.",
            "is_pending": False,
        }

    # 선택된 주문 표시명 생성
    selected_order = next(o for o in orders if o.id == order_id)
    summary = _format_order_summary(db, selected_order)
    order_display = (
        f"주문 번호 #{order_id} · {summary} · 주문일 {selected_order.created_at.strftime('%Y-%m-%d')}"
    )

    return {**state, "order_id": order_id, "order_display": order_display}


async def select_items(state: OrderState, config: RunnableConfig) -> dict:
    """교환 플로우: 교환 품목 선택 → interrupt."""
    from app.models.order import OrderItem
    from app.models.product import Product

    db = _get_db(config)
    order_items = (
        db.query(OrderItem)
        .filter(OrderItem.order_id == state["order_id"])
        .all()
    )

    if not order_items:
        return {**state, "abort": True, "response": "해당 주문의 상품을 조회할 수 없습니다.", "is_pending": False}

    item_lines = []
    for i, oi in enumerate(order_items):
        product = db.query(Product).filter(Product.id == oi.product_id).first()
        name = product.name if product else f"상품 #{oi.product_id}"
        item_lines.append(f"{i + 1}. {name} × {oi.quantity}개")
    item_list = "\n".join(item_lines)

    prompt = ORDER_PROMPTS["select_items"].format(
        order_display=state["order_display"],
        item_list=item_list,
    )

    # ── interrupt: 교환 품목 선택 대기 ────────────────────────────────────
    user_input = interrupt(prompt)

    if _is_cancel_intent(user_input):
        return {**state, "abort": True, "response": ORDER_PROMPTS["flow_cancelled"], "is_pending": False}

    # 사용자 입력 파싱 — 번호 기반 또는 "전체"
    selected = []
    if "전체" in user_input:
        for i, oi in enumerate(order_items):
            product = db.query(Product).filter(Product.id == oi.product_id).first()
            name = product.name if product else f"상품 #{oi.product_id}"
            selected.append({"item_id": oi.id, "product_id": oi.product_id, "name": name, "qty": oi.quantity})
    else:
        nums = re.findall(r"\d+", user_input)
        for num_str in nums:
            n = int(num_str)
            if 1 <= n <= len(order_items):
                oi = order_items[n - 1]
                product = db.query(Product).filter(Product.id == oi.product_id).first()
                name = product.name if product else f"상품 #{oi.product_id}"
                # 수량 파싱: "2개" 같은 패턴 찾기 (없으면 전량)
                qty_match = re.search(r"(\d+)\s*개", user_input)
                qty = int(qty_match.group(1)) if qty_match else oi.quantity
                qty = min(qty, oi.quantity)
                selected.append({"item_id": oi.id, "product_id": oi.product_id, "name": name, "qty": qty})
        # 아무것도 선택 못하면 전체 선택
        if not selected:
            for oi in order_items:
                product = db.query(Product).filter(Product.id == oi.product_id).first()
                name = product.name if product else f"상품 #{oi.product_id}"
                selected.append({"item_id": oi.id, "product_id": oi.product_id, "name": name, "qty": oi.quantity})

    return {**state, "selected_items": selected}


async def check_stock(state: OrderState, config: RunnableConfig) -> dict:
    """교환 품목 재고 확인 (자동 — interrupt 없음).

    재고 부족 시 수량 조정 안내를 응답에 포함하고 진행합니다.
    오피스 팀에서 최종 처리 시 재확인하므로 챗봇은 안내만 합니다.
    """
    from app.models.product import Product

    db = _get_db(config)
    notes = []

    for item in state["selected_items"]:
        product = db.query(Product).filter(Product.id == item["product_id"]).first()
        if product and product.stock < item["qty"]:
            if product.stock == 0:
                notes.append(f"• {item['name']}: 현재 재고 없음 (접수는 가능하며 오피스 확인 후 처리됩니다)")
            else:
                notes.append(f"• {item['name']}: 현재 재고 {product.stock}개 (요청 {item['qty']}개)")

    # 재고 노트는 summary에서 보여주므로 state에만 저장
    stock_note = "\n".join(notes) if notes else ""
    return {**state, "_stock_note": stock_note} if stock_note else state


async def get_reason(state: OrderState, config: RunnableConfig) -> dict:
    """교환/취소 사유 선택 → interrupt."""
    prompt_key = "cancel_reason" if state["action"] == "cancel" else "exchange_reason"
    reason_map = CANCEL_REASON_MAP if state["action"] == "cancel" else EXCHANGE_REASON_MAP
    prompt = ORDER_PROMPTS[prompt_key]

    # ── interrupt: 사유 입력 대기 ────────────────────────────────────────
    user_input = interrupt(prompt)

    if _is_cancel_intent(user_input):
        return {**state, "abort": True, "response": ORDER_PROMPTS["flow_cancelled"], "is_pending": False}

    reason = _parse_reason(user_input, reason_map)
    return {**state, "reason": reason}


async def get_refund_method(state: OrderState, config: RunnableConfig) -> dict:
    """취소 플로우만: 환불 방법 선택 → interrupt."""
    prompt = ORDER_PROMPTS["refund_method"]

    # ── interrupt: 환불 방법 대기 ────────────────────────────────────────
    user_input = interrupt(prompt)

    if _is_cancel_intent(user_input):
        return {**state, "abort": True, "response": ORDER_PROMPTS["flow_cancelled"], "is_pending": False}

    refund_method = _parse_refund_method(user_input)
    return {**state, "refund_method": refund_method}


async def show_summary(state: OrderState, config: RunnableConfig) -> dict:
    """최종 내용 요약 → interrupt로 최종 승인 대기."""
    if state["action"] == "cancel":
        prompt = ORDER_PROMPTS["cancel_summary"].format(
            order_display=state.get("order_display", ""),
            reason=state.get("reason", ""),
            refund_method=state.get("refund_method", "원결제 수단 환불"),
        )
    else:
        items_display = "\n".join(
            f"  • {item['name']} × {item['qty']}개"
            for item in state.get("selected_items", [])
        )
        stock_note = state.get("_stock_note", "")  # type: ignore[typeddict-item]
        if stock_note:
            items_display += f"\n\n재고 안내:\n{stock_note}"

        prompt = ORDER_PROMPTS["exchange_summary"].format(
            order_display=state.get("order_display", ""),
            items_display=items_display,
            reason=state.get("reason", ""),
        )

    # ── interrupt: 최종 승인 대기 ────────────────────────────────────────
    user_input = interrupt(prompt)

    confirmed = _is_confirm_intent(user_input) and not _is_cancel_intent(user_input)
    return {**state, "confirmed": confirmed}


async def create_ticket(state: OrderState, config: RunnableConfig) -> dict:
    """티켓 발행 — DB INSERT."""
    from app.models.ticket import ShopTicket
    from app.models.order import Order

    db = _get_db(config)

    # 소유권 재검증 — defense-in-depth (list_orders에서 이미 필터했으나 재확인)
    order = db.query(Order).filter(
        Order.id == state["order_id"],
        Order.user_id == state["user_id"],
    ).first()
    if not order:
        logger.warning(
            f"[order_graph] 소유권 검증 실패: user={state['user_id']} order={state['order_id']}"
        )
        return {**state, "response": "주문 정보를 확인할 수 없습니다.", "is_pending": False}

    items_json = json.dumps(state.get("selected_items", []), ensure_ascii=False) or None

    ticket = ShopTicket(
        user_id=state["user_id"],
        session_id=state.get("session_id"),
        order_id=state["order_id"],
        action_type=state["action"],
        reason=state.get("reason") or "기타",
        refund_method=state.get("refund_method"),
        items=items_json if state["action"] == "exchange" else None,
        status="received",
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    logger.info(
        f"[order_graph] 티켓 발행: #{ticket.id} "
        f"user={state['user_id']} action={state['action']} order={state['order_id']}"
    )

    response = ORDER_PROMPTS["ticket_created"].format(ticket_id=ticket.id)
    return {**state, "ticket_id": ticket.id, "response": response, "is_pending": False}


async def handle_flow_cancel(state: OrderState, config: RunnableConfig) -> dict:
    """플로우 중단 처리 (사용자 취소 또는 오류)."""
    # response가 이미 설정된 경우 그대로 사용
    response = state.get("response") or ORDER_PROMPTS["flow_cancelled"]
    return {**state, "response": response, "is_pending": False}


# ── 조건부 라우팅 함수 ─────────────────────────────────────────────────────────

def route_after_list_orders(state: OrderState) -> str:
    """list_orders 이후 분기."""
    if state.get("abort"):
        return "handle_flow_cancel"
    return "select_items" if state["action"] == "exchange" else "get_reason"


def route_after_get_reason(state: OrderState) -> str:
    """get_reason 이후 분기."""
    if state.get("abort"):
        return "handle_flow_cancel"
    return "get_refund_method" if state["action"] == "cancel" else "show_summary"


def route_after_show_summary(state: OrderState) -> str:
    """show_summary 이후 분기."""
    if state.get("abort") or not state.get("confirmed"):
        return "handle_flow_cancel"
    return "create_ticket"


def route_abort_check(state: OrderState) -> str:
    """범용 abort 체크 (select_items, check_stock, get_refund_method 이후)."""
    return "handle_flow_cancel" if state.get("abort") else "__continue__"
