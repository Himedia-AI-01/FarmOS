# ai/agent/order_graph/

LangGraph `StateGraph` 기반 취소·교환 멀티스텝 HitL 플로우.  
`USE_MULTI_AGENT=true`일 때만 활성화됩니다.

---

## 왜 LangGraph인가

취소/교환은 여러 턴에 걸친 대화가 필요합니다.

```
사용자: "주문 취소하고 싶어"
봇: "어떤 주문인가요? [주문 목록]"
사용자: "2번"
봇: "취소 사유를 선택해주세요. [1.단순변심 2.배송지연 ...]"
사용자: "1"
봇: "환불 방법을 선택해주세요. [1.원결제수단 2.포인트]"
사용자: "1"
봇: "확인해주세요. [요약] 진행하시겠어요?"
사용자: "네"
봇: "티켓 #42 발행 완료"
```

기존 단일 에이전트의 `pending_action` 컬럼은 1단계 확인만 지원했습니다.  
LangGraph의 `checkpointer`가 **그래프 상태 전체**를 PostgreSQL에 저장하므로  
임의의 단계에서 중단하고, 다음 요청에서 정확히 그 지점부터 재개할 수 있습니다.

---

## 파일

| 파일 | 역할 |
|------|------|
| `state.py` | `OrderState` TypedDict — 그래프 전체 생애 동안 누적되는 상태 |
| `nodes.py` | 노드 함수 + 조건부 라우팅 함수 |
| `graph.py` | `build_order_graph(checkpointer)` — StateGraph 컴파일 |
| `prompts.py` | `ORDER_PROMPTS` 딕셔너리, `CANCEL_KEYWORDS`, `CONFIRM_KEYWORDS`, reason/method 맵 |

---

## OrderState 스키마

```python
class OrderState(TypedDict):
    # 요청 정보
    action: str              # "cancel" | "exchange"
    user_id: int
    session_id: int
    user_message: str        # interrupt resume 값 (사용자 답변)

    # 수집 데이터
    order_id: int | None
    order_display: str | None    # "주문 #12 — 딸기 2kg (2026-04-01)"
    selected_items: list         # 교환 품목 [{item_id, product_id, name, qty}]
    reason: str | None
    refund_method: str | None    # 취소 플로우만

    # 제어 플래그
    confirmed: bool | None   # None=미결, True=승인, False=거부
    abort: bool              # True → handle_flow_cancel로 즉시 분기
    is_pending: bool         # True=interrupt 대기 / False=플로우 종료

    # 결과
    ticket_id: int | None
    response: str            # 터미널 노드(create_ticket, handle_flow_cancel)가 채우는 최종 메시지
```

---

## 그래프 구조

### 취소 플로우

```
START
  → route_action
  → list_orders          [interrupt: 주문 선택]
  → get_reason           [interrupt: 취소 사유]
  → get_refund_method    [interrupt: 환불 방법]
  → show_summary         [interrupt: 최종 확인]
  → create_ticket        → END
```

### 교환 플로우

```
START
  → route_action
  → list_orders          [interrupt: 주문 선택]
  → select_items         [interrupt: 교환 품목 선택]
  → check_stock          [자동 — interrupt 없음]
  → get_reason           [interrupt: 교환 사유]
  → show_summary         [interrupt: 최종 확인]
  → create_ticket        → END
```

어느 단계에서든 사용자가 "취소" 키워드를 입력하면 `abort=True` → `handle_flow_cancel` → `END`.

---

## 노드 목록

| 노드 | interrupt | 역할 |
|------|:---------:|------|
| `route_action` | ✗ | 취소/교환 분기용 passthrough |
| `list_orders` | ✓ | DB: 최근 5개 주문 조회 → 주문 선택 대기 |
| `select_items` | ✓ | DB: 주문 품목 조회 → 교환 품목 선택 대기 (교환 전용) |
| `check_stock` | ✗ | DB: 재고 확인 + 부족 시 안내 노트 기록 (교환 전용) |
| `get_reason` | ✓ | 취소/교환 사유 선택 대기 |
| `get_refund_method` | ✓ | 환불 방법 선택 대기 (취소 전용) |
| `show_summary` | ✓ | 수집 정보 요약 + 최종 승인 대기 → `confirmed` 설정 |
| `create_ticket` | ✗ | DB: `ShopTicket` INSERT → `ticket_id` / `response` 설정 |
| `handle_flow_cancel` | ✗ | 플로우 중단 처리 → `response` = "취소됐습니다" |

---

## interrupt/resume 패턴 — 핵심 주의사항

LangGraph의 `interrupt(value)`는 예외를 발생시켜 그래프를 일시 중단합니다.  
**다음 `ainvoke()` 호출 시 노드는 처음부터 다시 실행되고**, `interrupt()`가 resume 값을 반환합니다.

```python
# list_orders 노드 — 두 번 실행됨
async def list_orders(state, config):
    db = config["configurable"]["db"]
    orders = db.query(Order).filter(...).all()   # ← 두 번째 실행에서도 다시 조회 (읽기 전용 OK)

    prompt = "어떤 주문인가요? ..."
    user_input = interrupt(prompt)               # ← 첫 실행: 여기서 중단
                                                 #    두 번째 실행: resume 값 반환
    order_id = _parse_order_selection(user_input, orders)
    return {..., "order_id": order_id}
```

**따라서:**
- `interrupt()` 이전에 DB 쓰기를 하면 두 번 실행됩니다 — 금지
- DB 읽기는 멱등이므로 안전합니다

---

## DB 주입 패턴

LangGraph는 노드에 `config: RunnableConfig`를 전달합니다.  
`SupervisorExecutor._call_order_agent()`에서 `config`를 구성할 때 `db`를 주입합니다.

```python
# SupervisorExecutor
config = {
    "configurable": {
        "thread_id": str(session_id),   # LangGraph checkpointer 키
        "db": db,                        # SQLAlchemy Session 주입
    }
}
await self.order_graph.ainvoke(initial_state, config)

# 노드 내부
def _get_db(config: RunnableConfig):
    return config["configurable"]["db"]
```

---

## checkpointer 설정

`AsyncPostgresSaver`를 사용합니다 (psycopg3 기반, psycopg2와 별도).  
앱 시작 시 `checkpointer.setup()`이 LangGraph 전용 테이블을 자동 생성합니다.

```python
# app/main.py (USE_MULTI_AGENT=true 시)
async with AsyncPostgresSaver.from_conn_string(settings.database_url) as checkpointer:
    await checkpointer.setup()      # checkpoints, checkpoint_writes 테이블 생성
    order_graph = build_order_graph(checkpointer)
    ...
    yield   # ← yield는 반드시 async with 안에 있어야 checkpointer 연결 유지
```

`thread_id = str(session_id)` — 대화 세션과 그래프 상태가 1:1 대응합니다.

---

## 진행 중 플로우 재개

`SupervisorExecutor._run_loop()` 진입 시 `_has_pending_order_flow(session_id)`를 먼저 확인합니다.  
진행 중인 플로우가 있으면 **Supervisor LLM 판단 없이** 즉시 OrderGraph로 전달합니다.

```python
if session_id and await self._has_pending_order_flow(session_id):
    response = await self._call_order_agent(user_message, ...)
    return AgentResult(answer=response, ...)
```

이렇게 하지 않으면 Supervisor LLM이 interrupt 질문을 리포맷하거나  
다른 에이전트를 잘못 호출하는 문제가 생깁니다.

---

## 주문 상태 필터

취소/교환 가능 여부를 제한하는 상수가 `nodes.py`에 정의됩니다.

```python
CANCELLABLE_STATUSES: frozenset[str] = frozenset({"pending", "registered"})
# 취소: 배송사 픽업 전(결제 완료·배송 준비 중) 단계만

EXCHANGEABLE_STATUSES: frozenset[str] = frozenset({"delivered"})
# 교환: 배송 완료된 주문만

_STATUS_DISPLAY: dict[str, str] = {
    "pending":    "결제 완료 (배송 준비 전)",
    "registered": "배송 준비 중",
    "picked_up":  "배송 중 (픽업 완료)",
    "in_transit": "배송 중",
    "delivered":  "배송 완료",
    "cancelled":  "취소 완료",
}
```

`list_orders` 노드는 `action`에 따라 해당 상수로 필터링합니다.  
이미 배송된 주문이 취소 목록에 나타나거나, 배송 중인 주문이 교환 목록에 나타나는 버그를 방지합니다.

---

## ShippingTracker 자동 상태 전환 범위

`app/services/shipping_tracker.py`는 APScheduler로 주기적으로 실행됩니다.  
배송 지연·내부 사정으로 인해 **자동 완료 처리는 금지**되어 있습니다.

```python
# 자동 전환 최대 단계: in_transit (delivered는 관리자 직접 처리)
if days_elapsed >= 2:   new_status = "in_transit"
elif days_elapsed >= 1: new_status = "picked_up"
else:                   new_status = "registered"
```

`delivered` 상태는 절대 자동으로 전환되지 않습니다 — 관리자 직접 처리 필요.

---

## ShopTicket 모델

`create_ticket` 노드가 플로우 완료 시 `shop_tickets` 테이블에 저장합니다.

| 컬럼 | 타입 | 내용 |
|------|------|------|
| `id` | int PK | 티켓 번호 |
| `user_id` | int FK | 고객 |
| `session_id` | int FK? | 채팅 세션 (`shop_chat_sessions.id`, nullable) |
| `order_id` | int FK | 대상 주문 |
| `action_type` | str(20) | "cancel" \| "exchange" |
| `reason` | Text | 사유 |
| `refund_method` | str(50)? | 환불 방법 (취소 전용) |
| `items` | Text? | JSON 배열 (교환 전용) |
| `status` | str(30) | "received" → "processing" → "completed" \| "cancelled" |
| `created_at` | datetime | 접수 시각 |
