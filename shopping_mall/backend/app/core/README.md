# app/core/

애플리케이션 전역 설정.

## 파일

| 파일 | 역할 |
|------|------|
| `config.py` | 환경변수를 한 곳에서 관리하는 `Settings` 클래스 |

## 규칙

환경변수는 각 파일에서 `os.getenv()`로 직접 읽지 않는다.
반드시 `Settings`에 필드로 추가하고 `from app.core.config import settings`로 참조한다.

```python
from app.core.config import settings

engine = create_engine(settings.database_url)
```

## Settings 필드 목록

| 필드 | 환경변수 | 설명 |
|------|----------|------|
| `database_url` | `DATABASE_URL` | PostgreSQL 접속 URL |
| `jwt_secret_key` | `JWT_SECRET_KEY` | FarmOS 공유 JWT 시크릿 |
| `litellm_url` | `LITELLM_URL` | OpenAI 호환 LLM 엔드포인트 |
| `litellm_api_key` | `LITELLM_API_KEY` | LLM API 키 |
| `litellm_model` | `LITELLM_MODEL` | LLM 모델명 |
| `agent_max_iterations` | `AGENT_MAX_ITERATIONS` | 에이전트 최대 반복 횟수 |
| `anniversary_api_key` | `ANNIVERSARY_API_KEY` | 공공데이터 공휴일 API 키 |
| `reranker_model` | `RERANKER_MODEL` | Cross-Encoder 재랭킹 모델 (기본: `dragonkue/bge-reranker-v2-m3-ko`) |
| `rag_distance_threshold` | `RAG_DISTANCE_THRESHOLD` | Dense 벡터 검색 거리 임계값 — FAQ·정책 공통 (기본: `0.50`) |
| `rag_storage_distance_threshold` | `RAG_STORAGE_DISTANCE_THRESHOLD` | 보관법 첫 번째 검색 임계값 (기본: `0.35`) |
| `rag_storage_retry_threshold` | `RAG_STORAGE_RETRY_THRESHOLD` | 보관법 재시도 임계값 (기본: `0.40`) |
| `embed_provider` | `EMBED_PROVIDER` | 임베딩 provider (`openrouter` / `sentence_transformers` / `openai`) |
| `embed_model` | `EMBED_MODEL` | 임베딩 모델명 — 비우면 provider 기본값 사용 |
| `langgraph_postgres_url` | *(computed)* | `database_url`에서 SQLAlchemy 드라이버 접미사 제거한 psycopg3 전용 URL |

민감한 값(API 키, 시크릿 키, DB URL)은 기본값을 `""`으로 두고 실제 값은 `.env`에서만 제공한다.

> `rag_distance_threshold` 계열 값은 임베딩 모델 교체 시 반드시 재측정해야 합니다.  
> 모델을 변경하면 거리 공간이 달라지므로 기존 임계값을 그대로 사용하면 검색 품질이 저하됩니다.
