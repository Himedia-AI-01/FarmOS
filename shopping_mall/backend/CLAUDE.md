# 쇼핑몰 백엔드 — Claude 레퍼런스 카드

포트 4000 | FastAPI + PostgreSQL + ChromaDB | 패키지: `uv`

---

## 코드 작성 규칙

### 환경변수 처리
- 환경변수는 각 파일에서 `os.getenv()`로 직접 읽지 않는다.
- **반드시** `app/core/config.py`의 `Settings` 클래스에 필드로 추가하고 `from app.core.config import settings`로 참조한다.
- 민감한 값(API 키, 시크릿 키, DB URL 등)은 기본값을 `""`으로 두고 실제 값은 `.env`에서만 제공한다.

### 경로 상수
경로를 하드코딩하지 말고 `from app.paths import ...` 사용.

```python
PROJECT_ROOT    # FarmOS/
LOG_DIR         # FarmOS/logs/
CHROMA_DB_PATH  # .../backend/chroma_data/
AI_DATA_DIR     # .../backend/ai/data/
```

정책 문서 폴더는 `paths.py`가 아닌 `settings.policy_docs_dir`로 접근합니다 (`.env`의 `POLICY_DOCS_DIR` 반영).

---

## 챗봇 에이전트

tool_use 기반 에이전트. Primary LLM 장애 시 Claude로 자동 전환.

```
OpenAIAgentClient (PRIMARY_LLM_*)
  → 실패 시 ClaudeAgentClient (ANTHROPIC_API_KEY)
    → 둘 다 실패 시 escalated=True
```

**아키텍처**: `SupervisorExecutor` 오케스트레이터 → CS 서브 에이전트(10개 도구) + OrderGraph(LangGraph, 취소/교환 멀티스텝 HitL), `MultiAgentChatbotService`

### 핵심 파일 맵

**공통 인프라**

| 파일 | 역할 |
|------|------|
| `ai/agent/clients/base.py` | AgentClient 추상 인터페이스 |
| `ai/agent/clients/openai.py` | Primary LLM — OpenAI 호환 |
| `ai/agent/clients/claude.py` | Fallback LLM — Anthropic SDK |
| `ai/agent/executor.py` | AgentExecutor 루프, RequestContext, TraceStep, ToolMetricData |
| `ai/agent/tools.py` | TOOL_DEFINITIONS 9개, TOOL_TO_INTENT |
| `ai/agent/holiday.py` | 공휴일 API + 월별 캐시 |
| `ai/rag.py` | retrieve() / retrieve_multiple() + distance_threshold 필터 |
| `app/core/config.py` | Settings 클래스 — 전체 환경변수 관리 |
| `app/main.py` | 에이전트 초기화 + UTF-8 로그 핸들러 |
| `app/routers/chatbot.py` | POST /api/chatbot/ask?debug=true |

**에이전트 구조**

| 파일 | 역할 |
|------|------|
| `ai/agent/supervisor/executor.py` | SupervisorExecutor — 오케스트레이션 루프 |
| `ai/agent/supervisor/tools.py` | SUPERVISOR_TOOLS (call_cs_agent, call_order_agent) |
| `ai/agent/supervisor/prompts.py` | SUPERVISOR_SYSTEM_PROMPT |
| `ai/agent/subagents/cs/tools.py` | CS_TOOLS — 9개 도구 서브셋 |
| `ai/agent/subagents/cs/prompts.py` | CS_AGENT_SYSTEM_PROMPT |
| `ai/agent/order_graph/state.py` | OrderState TypedDict |
| `ai/agent/order_graph/nodes.py` | 노드 함수 + 조건부 라우팅 (interrupt/resume 패턴) |
| `ai/agent/order_graph/graph.py` | build_order_graph(checkpointer) |
| `ai/agent/order_graph/prompts.py` | ORDER_PROMPTS, CANCEL_KEYWORDS, CONFIRM_KEYWORDS |
| `app/services/multi_agent_chatbot.py` | MultiAgentChatbotService — ChatLog/ToolMetric 저장 포함 |
| `app/models/ticket.py` | ShopTicket — shop_tickets 테이블, 취소/교환 접수 결과 |

### 보안 규칙

`refuse_request` 도구 — 타인 정보·내부 정보·서비스 범위 외 질문·탈옥·부적절 콘텐츠를 필터링합니다.
도구가 `__REFUSED__\n사유: <코드>` 마커를 반환하면, 출력 LLM이 사유에 맞는 정중한 거절 메시지를 생성합니다.
reason 코드: `other_user_info` | `internal_info` | `out_of_scope` | `jailbreak` | `inappropriate`

`RequestContext.to_system_suffix()`는 로그인 여부(`로그인` / `비로그인`)만 LLM에 전달합니다. 실제 `user_id` 숫자는 시스템 프롬프트에 포함하지 않습니다 — LLM이 응답에서 내부 ID를 노출하는 것을 방지합니다.

`get_order_status` 호출 시 LLM이 `user_id`를 인자로 넘기면 **타인 정보 조회 시도**로 간주하고 즉시 거절합니다.
`get_order_status` 스키마에 `user_id` 파라미터가 없으므로, LLM이 이를 명시한다는 것은 사용자가 특정 user_id를 요청한 신호입니다.
```python
if "user_id" in args:
    return self._tool_refuse_request("other_user_info")   # 코드 레벨 차단
return await self._tool_get_order_status(db, user_id, **args)  # 서버 세션 user_id 사용
```

### 히스토리 역할 매핑

프론트엔드가 어시스턴트 메시지를 `role: "bot"` 으로 전송합니다. `_build_history` 에서 반드시 매핑:

```python
# multi_agent_chatbot.py
_ROLE_MAP = {"user": "user", "assistant": "assistant", "bot": "assistant"}
```

누락 시 LLM이 어시스턴트 답변을 받지 못해 이전 질문을 누적해서 재답변합니다.

### 시스템 프롬프트 필수 구성 (`ai/agent/prompts.py`)

1. **도구 선택 규칙** — 로그인 필요 도구와 불필요 도구를 명시적으로 분리
2. **정책 인용 원칙** — `[doc > 조]` 형식 출처 태그가 있으면 `(근거: ...)` 형식으로 반드시 인용
3. **내부 용어 노출 금지** — 도구 이름(`search_policy`, `get_order_status` 등), 필드명(`order_item_id`, `user_id`)을 고객 응답에 포함하면 안 됨

### RAG 임계값 (ko-sroberta-multitask 기준)

| 도구 | 컬렉션 | `distance_threshold` |
|------|--------|---------------------|
| `search_policy` | 정책 6종 | **0.65** (실측 0.51~0.62) |
| `search_faq` | `faq` | 0.45 |
| `search_storage_guide` | `storage_guide` | 0.40~0.45 |

임베딩 모델 변경 시 반드시 재측정 → `/rag-diagnostic` 스킬 참고.

### TraceStep

- `FarmOS/logs/chatbot.log` — INFO 레벨 항상 기록
- `POST /api/chatbot/ask?debug=true` — 응답 `trace` 필드에 포함

---

## 환경변수 (.env)

| 변수 | 설명 |
|------|------|
| `DATABASE_URL` | PostgreSQL 접속 URL |
| `JWT_SECRET_KEY` | FarmOS 공유 JWT 시크릿 |
| `OLLAMA_BASE_URL` | Ollama 서버 주소 — 임베딩 전용 (기본: `http://localhost:11434`) |
| `OLLAMA_EMBED_MODEL` | ChromaDB 임베딩 모델 |
| `UTILITY_LLM_BASE_URL` | 리포트/비용분류용 LLM 엔드포인트 (Ollama·OpenRouter 모두 가능) |
| `UTILITY_LLM_API_KEY` | 리포트/비용분류용 LLM API 키 |
| `UTILITY_LLM_MODEL` | 리포트/비용분류용 LLM 모델명 |
| `PRIMARY_LLM_BASE_URL` | 에이전트 Primary LLM 엔드포인트 (Ollama·OpenRouter 모두 가능) |
| `PRIMARY_LLM_API_KEY` | Primary LLM API 키 |
| `PRIMARY_LLM_MODEL` | Primary LLM 모델명 |
| `ANTHROPIC_API_KEY` | Fallback LLM — 없으면 폴백 비활성화 |
| `CLAUDE_FALLBACK_MODEL` | Claude Fallback 모델 (기본: `claude-haiku-4-5`) |
| `AGENT_MAX_ITERATIONS` | 에이전트 최대 반복 횟수 (기본: `10`) |
| `ANNIVERSARY_API_KEY` | 공공데이터포털 공휴일 API 키 |
| `POLICY_DOCS_DIR` | 정책 문서 폴더 (기본: `ai/docs/`, gitignore — 로컬 배치 필요) |

Provider 전환은 `PRIMARY_LLM_*` 세 값만 교체하면 됩니다. 자세한 예시는 `ai/agent/clients/README.md` 참고.

---

## 자주 쓰는 명령

```bash
# 서버 실행
uv run uvicorn app.main:app --reload --port 4000

# RAG 시딩 + 검증
uv run python scripts/seed_and_verify.py

# 테스트
uv run pytest

# 챗봇 API 테스트
curl -X POST http://localhost:4000/api/chatbot/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"딸기 재고 있어?","session_id":null,"history":[]}'

# 추론 과정 확인
curl -X POST "http://localhost:4000/api/chatbot/ask?debug=true" \
  -H "Content-Type: application/json" \
  -d '{"question":"주문 배송 언제와?","session_id":null,"history":[]}'
```

---

## 상세 문서

| 위치 | 내용 |
|------|------|
| `ai/README.md` | AI 모듈 전체 구조, RAG 셋업 가이드 |
| `ai/agent/README.md` | 운영 모드, 도구 전체 목록, AgentExecutor 동작 원리 |
| `ai/agent/supervisor/README.md` | SupervisorExecutor 오케스트레이션, 도구 선택 기준, 진행 중 플로우 처리 |
| `ai/agent/order_graph/README.md` | LangGraph interrupt/resume 패턴, 취소·교환 플로우, DB 주입, ShopTicket |
| `ai/agent/subagents/cs/README.md` | CS_TOOLS 구성, CS 에이전트 제약 사항 |
| `ai/agent/clients/README.md` | LLM 클라이언트, provider 전환 예시 |
| `app/core/README.md` | Settings 필드 전체 목록 |
| `app/services/README.md` | 서비스 레이어 구조 |
| `/chatbot-agent` 스킬 | 챗봇 에이전트 전체 기획 문서 |
| `/rag-diagnostic` 스킬 | RAG 검색 이상 시 진단 절차 (컬렉션 확인·거리 측정·재시딩) |
| `/agent-pipeline-test` 스킬 | 서버 없이 에이전트 파이프라인 전체 검증 (도구 선택·인용·내부 용어) |
