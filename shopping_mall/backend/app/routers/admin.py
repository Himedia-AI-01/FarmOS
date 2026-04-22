"""관리자 전용 라우터 — 티켓·챗봇 로그·배송 운영 데이터."""
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.chat_log import ChatLog
from app.models.order import Order
from app.models.shipment import Shipment
from app.models.ticket import ShopTicket
from app.models.user import User
from app.services.shipping_tracker import ShippingTracker

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ── 스키마 ──────────────────────────────────────────────────────────────────


class TicketResponse(BaseModel):
    id: int
    user_id: int
    session_id: Optional[int] = None
    order_id: int
    action_type: str
    reason: str
    refund_method: Optional[str] = None
    items: Optional[str] = None  # JSON 문자열 배열
    status: str
    created_at: datetime
    user_name: Optional[str] = None
    order_total: Optional[int] = None

    model_config = {"from_attributes": True}


class TicketStatusUpdate(BaseModel):
    status: str  # "received" | "processing" | "completed" | "cancelled"


class AdminChatLogResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    session_id: Optional[int] = None
    intent: str
    question: str
    answer: str
    escalated: bool
    rating: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminChatSessionResponse(BaseModel):
    id: int
    user_id: int
    title: Optional[str] = None
    status: str
    log_count: int
    last_question: Optional[str] = None
    last_message_at: Optional[datetime] = None
    has_escalation: bool
    # None=미처리 없음 / "received"=접수됨 / "processing"=처리중 (가장 심각한 상태)
    pending_ticket_status: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── 헬퍼 ────────────────────────────────────────────────────────────────────

_VALID_TICKET_STATUSES = {"received", "processing", "completed", "cancelled"}

# 허용된 상태 전환 — 종료 상태(completed, cancelled)에서는 전환 불가
_TICKET_ALLOWED_TRANSITIONS: dict[str, list[str]] = {
    "received":   ["processing", "cancelled"],
    "processing": ["completed", "cancelled"],
    "completed":  [],
    "cancelled":  [],
}


def _enrich_ticket(t: ShopTicket, db: Session) -> TicketResponse:
    """lazy="raise" 관계를 별도 쿼리로 보완한다."""
    user = db.query(User).filter(User.id == t.user_id).first()
    order = db.query(Order).filter(Order.id == t.order_id).first()
    return TicketResponse(
        id=t.id,
        user_id=t.user_id,
        session_id=t.session_id,
        order_id=t.order_id,
        action_type=t.action_type,
        reason=t.reason,
        refund_method=t.refund_method,
        items=t.items,
        status=t.status,
        created_at=t.created_at,
        user_name=user.name if user else None,
        order_total=order.total_price if order else None,
    )


# ── 티켓 엔드포인트 ──────────────────────────────────────────────────────────


@router.get("/tickets", response_model=List[TicketResponse])
def list_tickets(
    status: Optional[str] = Query(None, description="received | processing | completed | cancelled"),
    action_type: Optional[str] = Query(None, description="cancel | exchange"),
    user_id: Optional[int] = Query(None, description="특정 사용자의 티켓만 조회"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """모든 교환·취소 티켓 목록을 반환합니다."""
    q = db.query(ShopTicket)
    if status:
        q = q.filter(ShopTicket.status == status)
    if action_type:
        q = q.filter(ShopTicket.action_type == action_type)
    if user_id is not None:
        q = q.filter(ShopTicket.user_id == user_id)
    tickets = q.order_by(desc(ShopTicket.created_at)).offset(offset).limit(limit).all()
    return [_enrich_ticket(t, db) for t in tickets]


@router.get("/tickets/stats")
def get_ticket_stats(db: Session = Depends(get_db)):
    """상태별 티켓 건수를 반환합니다."""
    rows = db.query(ShopTicket.status, ShopTicket.action_type).all()
    stats: dict = {
        "received": 0,
        "processing": 0,
        "completed": 0,
        "cancelled": 0,
        "total": 0,
        "exchange": 0,
        "cancel": 0,
    }
    for status, action_type in rows:
        stats["total"] += 1
        if status in stats:
            stats[status] += 1
        if action_type in stats:
            stats[action_type] += 1
    return stats


@router.get("/tickets/{ticket_id}", response_model=TicketResponse)
def get_ticket(ticket_id: int, db: Session = Depends(get_db)):
    t = db.query(ShopTicket).filter(ShopTicket.id == ticket_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="티켓을 찾을 수 없습니다.")
    return _enrich_ticket(t, db)


@router.patch("/tickets/{ticket_id}/status", response_model=TicketResponse)
def update_ticket_status(
    ticket_id: int,
    body: TicketStatusUpdate,
    db: Session = Depends(get_db),
):
    """티켓 상태를 변경합니다 (received → processing → completed | cancelled)."""
    if body.status not in _VALID_TICKET_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"유효하지 않은 상태: {body.status}. 허용값: {', '.join(_VALID_TICKET_STATUSES)}",
        )
    t = db.query(ShopTicket).filter(ShopTicket.id == ticket_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="티켓을 찾을 수 없습니다.")
    allowed = _TICKET_ALLOWED_TRANSITIONS.get(t.status, [])
    if body.status not in allowed:
        allowed_str = ", ".join(allowed) if allowed else "없음 (종료 상태)"
        raise HTTPException(
            status_code=400,
            detail=f"'{t.status}' → '{body.status}' 전환은 허용되지 않습니다. 허용된 전환: {allowed_str}",
        )
    t.status = body.status
    db.commit()
    db.refresh(t)
    return _enrich_ticket(t, db)


# ── 배송 엔드포인트 ──────────────────────────────────────────────────────────


class RelatedTicketSummary(BaseModel):
    id: int
    action_type: str
    status: str
    reason: str

    model_config = {"from_attributes": True}


class AdminShipmentResponse(BaseModel):
    id: int
    order_id: int
    carrier: str
    tracking_number: str
    status: str
    expected_arrival: Optional[datetime] = None
    last_checked_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    tracking_history: Optional[str] = None
    created_at: Optional[datetime] = None
    # 연관 정보
    order_total: Optional[int] = None
    related_ticket_id: Optional[int] = None  # 명시적 FK — None이면 원본 배송(교환 배송 아님)
    related_ticket: Optional[RelatedTicketSummary] = None

    model_config = {"from_attributes": True}


def _enrich_shipment(s: Shipment, db: Session) -> AdminShipmentResponse:
    order = db.query(Order).filter(Order.id == s.order_id).first()
    if s.related_ticket_id is not None:
        ticket = db.query(ShopTicket).filter(ShopTicket.id == s.related_ticket_id).first()
    else:
        ticket = (
            db.query(ShopTicket)
            .filter(
                ShopTicket.order_id == s.order_id,
                ShopTicket.action_type == "exchange",
            )
            .order_by(desc(ShopTicket.created_at))
            .first()
        )
    related = (
        RelatedTicketSummary(
            id=ticket.id,
            action_type=ticket.action_type,
            status=ticket.status,
            reason=ticket.reason,
        )
        if ticket
        else None
    )
    return AdminShipmentResponse(
        id=s.id,
        order_id=s.order_id,
        carrier=s.carrier,
        tracking_number=s.tracking_number,
        status=s.status,
        expected_arrival=s.expected_arrival,
        last_checked_at=s.last_checked_at,
        delivered_at=s.delivered_at,
        tracking_history=s.tracking_history,
        created_at=s.created_at,
        order_total=order.total_price if order else None,
        related_ticket_id=s.related_ticket_id,
        related_ticket=related,
    )


@router.get("/shipments", response_model=List[AdminShipmentResponse])
def admin_list_shipments(
    status: Optional[str] = Query(None, description="registered | picked_up | in_transit | delivered"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """배송 목록 + 연관 교환 티켓을 반환합니다."""
    q = db.query(Shipment)
    if status:
        q = q.filter(Shipment.status == status)
    shipments = q.order_by(desc(Shipment.created_at)).offset(offset).limit(limit).all()
    return [_enrich_shipment(s, db) for s in shipments]


@router.post("/shipments/{shipment_id}/check", response_model=AdminShipmentResponse)
def admin_check_shipment(shipment_id: int, db: Session = Depends(get_db)):
    """배송 상태를 수동으로 업데이트합니다."""
    s = db.query(Shipment).filter(Shipment.id == shipment_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="배송을 찾을 수 없습니다.")
    ShippingTracker.update_shipment(s)
    db.commit()
    db.refresh(s)
    return _enrich_shipment(s, db)


# ── 챗봇 로그 엔드포인트 (전체 사용자) ─────────────────────────────────────────


@router.get("/chatbot/logs", response_model=List[AdminChatLogResponse])
def admin_list_chat_logs(
    intent: Optional[str] = Query(None),
    escalated: Optional[bool] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """모든 사용자의 챗봇 로그를 반환합니다."""
    q = db.query(ChatLog)
    if intent:
        q = q.filter(ChatLog.intent == intent)
    if escalated is not None:
        q = q.filter(ChatLog.escalated == escalated)
    return q.order_by(desc(ChatLog.created_at)).offset(offset).limit(limit).all()


@router.get("/chatbot/logs/escalated", response_model=List[AdminChatLogResponse])
def admin_list_escalated_logs(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """에스컬레이션된 모든 챗봇 로그를 반환합니다."""
    return (
        db.query(ChatLog)
        .filter(ChatLog.escalated.is_(True))
        .order_by(desc(ChatLog.created_at))
        .limit(limit)
        .all()
    )


@router.get("/chatbot/sessions", response_model=List[AdminChatSessionResponse])
def admin_list_chat_sessions(
    escalated_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """모든 챗 세션 목록 (최근 업데이트 순)."""
    from app.models.chat_session import ChatSession

    sessions_q = db.query(ChatSession)
    if escalated_only:
        # DB 레벨에서 필터링 — offset/limit 전에 적용해야 페이지네이션이 정확함
        sessions_q = (
            sessions_q
            .join(ChatLog, ChatLog.session_id == ChatSession.id)
            .filter(ChatLog.escalated.is_(True))
            .distinct()
        )
    sessions = (
        sessions_q
        .order_by(desc(ChatSession.updated_at))
        .offset(offset)
        .limit(limit)
        .all()
    )

    result = []
    for s in sessions:
        last_log = (
            db.query(ChatLog)
            .filter(ChatLog.session_id == s.id)
            .order_by(desc(ChatLog.created_at))
            .first()
        )
        log_count = (
            db.query(ChatLog)
            .filter(ChatLog.session_id == s.id)
            .count()
        )
        # escalated_only=True 이면 JOIN 필터로 이미 보장 → 추가 쿼리 불필요
        has_escalation = escalated_only or (
            db.query(ChatLog)
            .filter(ChatLog.session_id == s.id, ChatLog.escalated.is_(True))
            .first()
        ) is not None

        # 이 세션에서 생성된 미처리 티켓 중 가장 심각한 상태 계산
        # received > processing (received가 더 긴급)
        session_tickets = (
            db.query(ShopTicket.status)
            .filter(
                ShopTicket.session_id == s.id,
                ShopTicket.status.in_(["received", "processing"]),
            )
            .all()
        )
        pending_ticket_status: Optional[str] = None
        if session_tickets:
            statuses = {row.status for row in session_tickets}
            pending_ticket_status = "received" if "received" in statuses else "processing"

        result.append(AdminChatSessionResponse(
            id=s.id,
            user_id=s.user_id,
            title=s.title,
            status=s.status,
            log_count=log_count,
            last_question=last_log.question if last_log else None,
            last_message_at=last_log.created_at if last_log else None,
            has_escalation=has_escalation,
            pending_ticket_status=pending_ticket_status,
            created_at=s.created_at,
            updated_at=s.updated_at,
        ))
    return result


@router.get("/chatbot/sessions/{session_id}/logs", response_model=List[AdminChatLogResponse])
def admin_get_session_logs(
    session_id: int,
    db: Session = Depends(get_db),
):
    """특정 세션의 모든 챗봇 로그를 시간 순으로 반환합니다."""
    from app.models.chat_session import ChatSession

    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return (
        db.query(ChatLog)
        .filter(ChatLog.session_id == session_id)
        .order_by(ChatLog.created_at.asc())
        .all()
    )
