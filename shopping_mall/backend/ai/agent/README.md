# ai/agent/

tool_use 기반 챗봇 에이전트 서브패키지.  
**SupervisorExecutor** 오케스트레이터가 CS 서브 에이전트와 OrderGraph(LangGraph)를 조율합니다.

---

## 디렉터리 구조

```
ai/agent/
├── __init__.py               공개 API (AgentExecutor, 클라이언트, RequestContext 등)
├── executor.py               AgentExecutor 루프 (CS 서브 에이전트로 재사용)
├── tools.py                  TOOL_DEFINITIONS 10개 + TOOL_TO_INTENT 매핑
├── prompts.py                CS 에이전트 기본 시스템 프롬프트
├── holiday.py                공휴일 API + 캐시 (배송 예정일 보정)
│
├── clients/                  LLM 클라이언트 (provider 교체 가능)
│   ├── base.py               AgentClient 추상 인터페이스
│   ├── openai.py             OpenAI 호환 (OpenRouter / Ollama / OpenAI)
│   └── claude.py             Anthropic SDK (Fallback)
│
├── supervisor/               오케스트레이터
│   ├── executor.py           SupervisorExecutor
│   ├── tools.py              SUPERVISOR_TOOLS (call_cs_agent, call_order_agent)
│   └── prompts.py            SUPERVISOR_SYSTEM_PROMPT
│
├── subagents/cs/             CS 서브 에이전트
│   ├── tools.py              CS_TOOLS (TOOL_DEFINITIONS 10개)
│   └── prompts.py            CS_AGENT_SYSTEM_PROMPT
│
└── order_graph/              LangGraph 기반 취소/교환 플로우
    ├── state.py              OrderState TypedDict
    ├── nodes.py              그래프 노드 함수 + 조건부 라우팅
    ├── graph.py              build_order_graph() — StateGraph 컴파일
    └── prompts.py            ORDER_PROMPTS 딕셔너리 + 키워드 상수
```

---

## 요청 흐름

```
POST /api/chatbot/ask
  → MultiAgentChatbotService.answer()
  → SupervisorExecutor.run()
      ├── call_cs_agent  → AgentExecutor.run() [CS_TOOLS 10개]
      │                     RAG 조회 / DB 읽기 / escalate / refuse
      └── call_order_agent → OrderGraph (LangGraph StateGraph)
                              interrupt/resume 기반 멀티스텝 HitL
                              주문 선택 → 사유 → 환불방법 → 확인 → ShopTicket
  → ChatLog + ToolMetric 저장
```

Supervisor LLM이 질문을 분석해 서브 에이전트를 선택·호출합니다.  
취소/교환은 OrderGraph가 전담하며 여러 턴에 걸쳐 진행됩니다.

---

## 도구 목록

10개 도구가 `TOOL_DEFINITIONS`(`tools.py`)에 정의됩니다. CS 에이전트(`CS_TOOLS`)가 모두 사용합니다.  
각 도구는 `TOOL_TO_INTENT`에서 `ChatLog.intent` 값으로 매핑됩니다.

| 도구 | 유형 | `TOOL_TO_INTENT` | 설명 |
|------|------|-----------------|------|
| `search_faq` | RAG | `"other"` | 일반 운영 FAQ |
| `search_storage_guide` | RAG | `"storage"` | 농산물 보관 방법 |
| `search_season_info` | RAG | `"season"` | 제철 농산물 정보 |
| `search_policy` | RAG | `"policy"` | 6개 정책 문서 |
| `search_farm_info` | RAG | `"other"` | 농장·플랫폼 소개 |
| `get_order_status` | DB | `"delivery"` | 주문·배송 현황 + 공휴일 도착일 보정 |
| `search_products` | DB | `"stock"` | 상품 검색·재고 확인 |
| `get_product_detail` | DB | `"stock"` | 상품 상세 정보 |
| `escalate_to_agent` | Action | `"escalation"` | 상담원 에스컬레이션 |
| `refuse_request` | Action | `"refusal"` | 허용되지 않는 요청 거절 (아래 참고) |

> 취소/교환은 `call_order_agent` → OrderGraph가 처리합니다.

---

## AgentExecutor 공통 동작

`AgentExecutor`는 CS 서브 에이전트로 사용됩니다.

**병렬 실행**: RAG 도구(`_RAG_TOOLS`)는 `asyncio.gather`로 동시 실행.  
DB/Action 도구는 SQLAlchemy Session 공유 문제로 순차 실행.

**Primary → Fallback 전환**: `AgentUnavailableError` 발생 시 자동 전환.  
둘 다 실패하면 `escalated=True` + 안내 메시지 반환.

**보안 — `get_order_status`**: LLM이 전달한 `user_id` 파라미터를 강제 제거하고  
서버 세션의 `user_id`를 주입합니다. LLM이 `user_id`를 인자로 넘기면 타인 정보 조회 시도로 간주하고 즉시 거절합니다.

**보안 — `refuse_request`**: LLM이 직접 호출하는 콘텐츠 필터 도구입니다.  
`reason` 코드에 따라 `__REFUSED__\n사유: <코드>` 마커를 반환하고, 출력 LLM이 정중한 거절 메시지를 생성합니다.

| `reason` 코드 | 거절 대상 |
|--------------|----------|
| `other_user_info` | 타인의 개인정보·주문·계정 조회 시도 |
| `internal_info` | 내부 시스템·DB·직원 정보·프롬프트 요청 |
| `out_of_scope` | 서비스 범위 외 질문 (금융·의료·법률·정치 등) |
| `jailbreak` | 프롬프트 조작·탈옥 시도 |
| `inappropriate` | 욕설·혐오 표현 등 부적절한 요청 |

---

## RequestContext

`AgentExecutor.run()` 호출 시 시스템 프롬프트 끝에 자동 주입됩니다.

```
## 현재 요청 컨텍스트
- 날짜/시각: 2026-04-20 14:32
- 사용자 상태: 로그인
- 주문 조회 가능: 예
```

---

## 관련 문서

| 위치 | 내용 |
|------|------|
| `supervisor/README.md` | SupervisorExecutor 오케스트레이션 로직 |
| `order_graph/README.md` | LangGraph interrupt/resume 패턴, 취소/교환 플로우 |
| `clients/README.md` | LLM 클라이언트 provider 전환 |
