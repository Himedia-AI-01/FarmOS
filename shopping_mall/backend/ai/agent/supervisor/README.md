# ai/agent/supervisor/

멀티 에이전트 오케스트레이터 (`USE_MULTI_AGENT=true`).  
Supervisor LLM이 tool_use 루프를 통해 CS 에이전트 또는 OrderGraph를 선택·호출합니다.

---

## 파일

| 파일 | 역할 |
|------|------|
| `executor.py` | `SupervisorExecutor` — 오케스트레이션 루프 |
| `tools.py` | `SUPERVISOR_TOOLS` — `call_cs_agent`, `call_order_agent` |
| `prompts.py` | `SUPERVISOR_SYSTEM_PROMPT` — 에이전트 선택 기준 |

---

## 두 도구

| 도구 | 실행 방식 | 담당 |
|------|:---------:|------|
| `call_cs_agent` | 병렬 (asyncio.gather) | 상품·보관법·제철·정책·FAQ·배송 현황 |
| `call_order_agent` | 순차 | 취소/교환 접수 (LangGraph StateGraph) |

CS 도구는 읽기 전용 + 독립적이므로 병렬 실행이 안전합니다.  
`call_order_agent`는 LangGraph + DB 쓰기를 포함하므로 순차 실행합니다.

---

## SupervisorExecutor 생성자

```python
SupervisorExecutor(
    primary: AgentClient,           # Primary LLM
    fallback: AgentClient | None,   # Fallback LLM
    cs_executor: AgentExecutor,     # CS 서브 에이전트
    cs_system_prompt: str,          # CS 서브 에이전트 시스템 프롬프트
    order_graph,                    # LangGraph CompiledStateGraph
    max_iterations: int = 5,        # Supervisor 루프 최대 반복 (서브 에이전트 호출 횟수)
)
```

`AgentExecutor`와 달리 `max_iterations`의 기본값이 5입니다.  
Supervisor는 LLM 판단 → 서브 에이전트 호출 1~2회면 충분하기 때문입니다.

---

## 요청 처리 흐름

```
SupervisorExecutor.run()
  │
  ├─ 1. _has_pending_order_flow(session_id) 확인
  │       └─ 진행 중인 OrderGraph 플로우가 있으면 ──────────────────────┐
  │                                                                      │
  ├─ 2. (없으면) Supervisor LLM 호출 (tool_use 루프)                    │
  │       │                                                              │
  │       ├─ call_cs_agent ──────────────────────────────────────────┐  │
  │       │    └─ AgentExecutor.run(query, CS_TOOLS, CS_SYSTEM_PROMPT) │  │
  │       │         └─ LLM tool_use 루프 (9개 CS 도구)                │  │
  │       │                                                            │  │
  │       └─ call_order_agent ─────────────────────────────────────┐  │  │
  │            └─ _call_order_agent()                               │  │  │
  │                 └─ OrderGraph.ainvoke()                         │  │  │
  │                                                                 ▼  ▼  ▼
  │
  └─ 3. 최종 답변 생성 (Supervisor LLM이 서브 에이전트 결과 통합)
         단, OrderGraph 질문은 그대로 전달 (리포맷 금지)
```

---

## 진행 중 플로우 처리 (`_has_pending_order_flow`)

OrderGraph가 `interrupt` 상태에서 대기 중일 때, 사용자가 다음 메시지를 보내면  
Supervisor LLM이 개입하지 않고 즉시 OrderGraph로 전달합니다.

```python
# _has_pending_order_flow()
snapshot = await order_graph.aget_state(config)
return bool(snapshot.next)   # next: 재개를 기다리는 노드 이름 목록
```

**이 체크가 없으면 생기는 문제:**
- Supervisor LLM이 "주문 취소 사유를 입력하세요" 같은 OrderGraph 질문을 요약하거나
  `call_cs_agent`로 잘못 분기할 수 있습니다.

---

## _call_order_agent 로직

```python
async def _call_order_agent(query, user_id, session_id, db):
    config = {"configurable": {"thread_id": str(session_id), "db": db}}
    snapshot = await order_graph.aget_state(config)

    if snapshot.next:
        # 진행 중인 플로우 재개
        await order_graph.ainvoke(Command(resume=query), config)
    else:
        # 신규 플로우: 취소/교환 의도 감지
        action = _detect_order_action(query)   # "cancel" | "exchange"
        initial_state = OrderState(action=action, user_id=user_id, ...)
        await order_graph.ainvoke(initial_state, config)

    new_snapshot = await order_graph.aget_state(config)

    if new_snapshot.next and new_snapshot.tasks:
        # interrupt 대기 중 → interrupt 메시지를 사용자에게 전달
        return str(new_snapshot.tasks[0].interrupts[0].value)
    else:
        # 플로우 완료 → 최종 response 반환
        return new_snapshot.values.get("response", "처리 완료")
```

---

## 의도 감지 (`_detect_order_action`)

쿼리에서 교환/취소 키워드 점수를 비교합니다.

```python
_CANCEL_KEYWORDS_ACTION   = {"취소", "cancel", "환불"}
_EXCHANGE_KEYWORDS_ACTION = {
    "교환", "exchange", "반품", "교체",
    # 상품 불량·하자 — 명시적 교환/반품 언급 없어도 교환 플로우로 유추
    "벌레", "이물질", "불량", "상함", "파손", "하자", "오배송",
    "썩음", "곰팡이", "망가", "깨짐", "냄새", "이상해", "상했",
}
# 교환 키워드 점수 > 취소 키워드 점수 → "exchange", 나머지 → "cancel"
```

**함축적 표현 처리**: "벌레가 나왔어", "상품이 상했어" 처럼 '교환'을 직접 언급하지 않아도
불량·하자 키워드가 있으면 교환 플로우로 유추합니다.

---

## 의도 불일치 감지 (`intent_mismatch`)

진행 중인 플로우와 새 요청의 의도가 다를 때 기존 플로우를 교체합니다.

```python
pending_action = snapshot.values.get("action") if snapshot.next else None
new_action = _detect_order_action(query)
intent_mismatch = (
    pending_action is not None
    and pending_action != new_action
    and any(kw in query for kw in _EXCHANGE_KEYWORDS_ACTION | _CANCEL_KEYWORDS_ACTION)
)

if snapshot.next and not intent_mismatch:
    # 진행 중인 플로우 재개
    await order_graph.ainvoke(Command(resume=query), config)
else:
    # 신규 플로우 시작 (또는 의도 불일치로 기존 플로우 교체)
    await order_graph.ainvoke(initial_state, config)
```

**없으면 생기는 문제**: 취소 플로우 진행 중 사용자가 "교환하고 싶어"라고 하면
취소 플로우가 그대로 재개되어 주문 취소 목록이 나타납니다.

---

## Supervisor LLM 역할 범위

Supervisor LLM은 **에이전트 선택만** 담당합니다. 직접 답변을 생성하지 않습니다.  
`SUPERVISOR_SYSTEM_PROMPT`에 "반드시 도구(에이전트)를 통해 처리하고, 직접 답변을 생성하지 마세요"가 명시되어 있습니다.

**CS 에이전트를 쓰는 경우:**
- 상품 재고·가격·보관법·제철
- 교환·환불 정책 안내 (실제 접수가 아닌 정책 설명)
- 배송 현황 조회, FAQ

**OrderGraph를 쓰는 경우:**
- 주문 취소 접수 (실제 처리)
- 주문 교환·반품 접수 (실제 처리)
- **상품 불량·하자 신고** (벌레, 이물질, 부패, 파손, 오배송 등) — 로그인 사용자라면 정책 안내 없이 바로 교환·반품 접수로 연결
- 반드시 로그인 사용자에게만

**비로그인 사용자의 교환/취소·불량 신고:**
→ `call_cs_agent`로 정책만 안내 (접수 불가)

---

## SUPERVISOR_TOOLS 설계 원칙 — query description 작성 가이드

`call_order_agent`처럼 멀티스텝 HitL 도구는 **query description을 잘못 쓰면 LLM이 직접 정보를 수집**하려 한다.

### 나쁜 예 (실제 발생한 버그)

```python
"description": "취소/교환 의도 + 사용자 답변 포함"
```

→ LLM이 아래처럼 쿼리를 직접 조립:
```
"주문번호 12번의 교환 접수 요청입니다. 교환 대상 상품의 상세 정보(상품명/옵션), 수량, 교환 사유..."
```

결과: `_detect_order_action` 오판 가능성 증가, 프론트엔드 UI 응답 지연.

### 올바른 예 (현재 적용)

```python
"description": (
    "사용자의 원문 메시지를 그대로 전달하세요. "
    "상세 정보를 수집하는 쿼리를 직접 만들지 마세요. "
    "OrderGraph가 단계별 interrupt로 필요한 정보를 직접 수집합니다."
),
```

**원칙**: HitL/interrupt 패턴 도구의 query description에는 "원문 전달" + "정보 수집은 도구 내부" 를 명시하라.
description이 구체적일수록 LLM이 그에 맞춰 query에 더 많은 내용을 담으려 한다.
