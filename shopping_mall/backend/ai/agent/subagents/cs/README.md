# ai/agent/subagents/cs/

CS(Customer Service) 서브 에이전트 — 정보 조회 전담.  
`AgentExecutor`를 그대로 재사용하고, 12개 도구 중 9개 서브셋(`CS_TOOLS`)만 제공합니다.

---

## 파일

| 파일 | 역할 |
|------|------|
| `tools.py` | `CS_TOOLS` — `TOOL_DEFINITIONS`에서 HitL 3개 제거 |
| `prompts.py` | `CS_AGENT_SYSTEM_PROMPT` |

---

## CS_TOOLS (9개)

```python
_CS_TOOL_NAMES = frozenset({
    "search_faq",
    "search_storage_guide",
    "search_season_info",
    "search_policy",
    "search_farm_info",       # RAG 5개
    "search_products",
    "get_product_detail",     # DB 읽기 2개
    "get_order_status",       # DB 읽기 1개 (배송 조회)
    "escalate_to_agent",      # Action 1개
})

CS_TOOLS = [t for t in TOOL_DEFINITIONS if t["name"] in _CS_TOOL_NAMES]
```

**제외된 도구 (3개):** `create_exchange_request`, `confirm_pending_action`, `cancel_pending_action`  
→ 멀티 에이전트 모드에서 취소/교환 접수는 OrderGraph가 전담합니다.

---

## CS_AGENT_SYSTEM_PROMPT 주요 지침

- 배송 현황은 로그인 사용자에게만 `get_order_status` 사용
- 비로그인 사용자의 배송 문의 → `search_policy(policy_type="delivery")`로 정책 안내
- 정책 인용 시 출처 태그 포함 (`(근거: ...)`)

### 교환·반품·불량 신고 응답 규칙

CS 에이전트는 교환/반품을 **직접 접수하지 않습니다**. 아래 형식으로만 응답합니다:

```text
[공감 한 문장]

교환과 반품·환불 중 원하시는 처리 방법을 알려주세요.

1. 교환 — 동일 상품으로 교체
2. 반품·환불 — 반품 후 환불 처리
```

고객이 방법을 선택하면 Supervisor가 자동으로 OrderGraph로 연결합니다.

**절대 금지 (CS_OUTPUT_PROMPT에 명시)**:
- 주문번호, 사진, 수량 등 정보 수집 폼 형태의 질문
- 응답에 `call_order_agent`, `search_policy`, `get_order_status` 등 내부 이름 노출
- "다음 단계", "별도 절차" 같은 내부 동작 언급

### 내부 용어 노출 검증

```python
FORBIDDEN = [
    'call_cs_agent', 'call_order_agent',
    'search_policy', 'search_faq', 'get_order_status',
    'order_item_id', 'user_id', 'session_id',
    '다음 단계', '접수 처리 단계', '별도 절차',
]
leaks = [t for t in FORBIDDEN if t in response]
assert not leaks, f'내부 용어 노출: {leaks}'
```

---

## 인스턴스화

`app/main.py`에서 `AgentExecutor`를 `CS_TOOLS`로 생성합니다.

```python
cs_executor = AgentExecutor(
    primary=primary,
    fallback=fallback,
    rag_service=rag,
    tools=CS_TOOLS,
    max_iterations=settings.agent_max_iterations,
)
supervisor = SupervisorExecutor(
    ...,
    cs_executor=cs_executor,
    cs_system_prompt=CS_AGENT_SYSTEM_PROMPT,
    ...
)
```

`SupervisorExecutor`가 `call_cs_agent` 도구를 실행할 때  
`cs_executor.run(user_message=query, system=cs_system_prompt, history=[])` 를 호출합니다.  
CS 에이전트는 히스토리 없이 Supervisor가 전달한 쿼리만 처리합니다.
