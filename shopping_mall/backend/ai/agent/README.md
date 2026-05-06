# ai/agent/

LangChain tool calling 기반 챗봇 에이전트 서브패키지.  
**SupervisorExecutor** 오케스트레이터가 CS 에이전트(`AgentExecutor`)와 OrderGraph(LangGraph)를 조율합니다.

---

## 디렉터리 구조

```
ai/agent/
├── __init__.py               공개 API (AgentExecutor, RequestContext, build_primary_llm 등)
├── executor.py               AgentExecutor — LangChain tool calling 루프
├── cs_tools.py               build_cs_tools() 팩토리 + 10개 StructuredTool + Pydantic 스키마
├── responses.py              사전 정의 응답 (Canned Responses) — LLM 없이 즉시 반환
├── llm.py                    LangChain LLM 팩토리 (ChatOpenAI) + LangSmith 환경 주입
├── prompts.py                CS 에이전트 기본 시스템 프롬프트
├── holiday.py                공휴일 API + 캐시 (배송 예정일 보정)
│
├── tone_policy.py            응답 톤앤매너 정책 계층 (BASE / CHATBOT / FAQ)
│
├── clients/                  ⚠️ 레거시 — Python 구현 제거됨, llm.py로 대체
│   └── README.md             이전 AgentClient 패턴 참고용
│
├── supervisor/               오케스트레이터
│   ├── executor.py           SupervisorExecutor — LangChain tool calling 루프
│   └── prompts.py            SUPERVISOR_INPUT_PROMPT / SUPERVISOR_OUTPUT_PROMPT
│
├── subagents/cs/             CS 서브 에이전트
│   └── prompts.py            CS_INPUT_PROMPT / CS_OUTPUT_PROMPT
│
├── faq_writer/               FAQ 자동 작성 에이전트
│   ├── agent.py              FaqWriterAgent
│   ├── tools.py              build_faq_writer_tools() 팩토리
│   └── prompts.py            FAQ_WRITER_SYSTEM_PROMPT
│
└── order_graph/              LangGraph 주문 처리 플로우
    ├── state.py              OrderState TypedDict
    ├── nodes.py              노드 함수 + 조건부 라우팅
    ├── graph.py              build_order_graph()
    └── prompts.py            ORDER_PROMPTS 딕셔너리
```

---

## 에이전트 계층 구조

```
SupervisorExecutor
  ├── AgentExecutor (CS 에이전트) — 정보 조회 전담
  │     └── build_cs_tools()  10개 StructuredTool
  └── OrderGraph (LangGraph)  — 취소/교환/변경 멀티스텝 HitL
```