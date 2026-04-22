"""애플리케이션 설정 — 환경변수를 한 곳에서 관리합니다."""
import re

from pydantic_settings import BaseSettings

from app.paths import BACKEND_ROOT


class Settings(BaseSettings):
    # ── 데이터베이스 ────────────────────────────────────────────────────────
    database_url: str = ""

    # ── 인증 ────────────────────────────────────────────────────────────────
    jwt_secret_key: str = ""

    # ── 임베딩 ──────────────────────────────────────────────────────────────
    # provider: openrouter | ollama | sentence_transformers | openai
    # ⚠️  시딩과 쿼리는 동일한 provider + model이어야 합니다. 변경 시 re-seed 필요.
    embed_provider: str = "ollama"
    # openrouter 기본값: openai/text-embedding-3-small (PRIMARY_LLM_API_KEY 재사용)
    # sentence_transformers 기본값: jhgan/ko-sroberta-multitask (한국어 특화)
    # openai 기본값: text-embedding-3-small
    # 비워두면 provider별 기본값 사용
    embed_model: str = ""
    embed_api_key: str = ""      # openai provider 전용 (openrouter는 primary_llm_api_key 재사용)
    embed_base_url: str = ""     # openai-compatible 엔드포인트 오버라이드 (선택)

    # ── Ollama (embed_provider=ollama 일 때 사용) ────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_embed_model: str = "embeddinggemma:latest"

    # ── Utility LLM (리포트/비용분류 — OpenAI 호환, Ollama·OpenRouter 모두 가능) ──
    utility_llm_base_url: str = "http://localhost:11434/v1"
    utility_llm_api_key: str = "ollama"
    utility_llm_model: str = "qwen2.5:7b"

    # ── Primary LLM (OpenAI 호환 — OpenRouter / Ollama / OpenAI 등) ─────────
    primary_llm_base_url: str = "https://openrouter.ai/api/v1"
    primary_llm_api_key: str = ""
    primary_llm_model: str = "google/gemma-3-27b-it"

    # ── Fallback LLM (Anthropic Claude) ─────────────────────────────────────
    anthropic_api_key: str = ""
    claude_fallback_model: str = "claude-haiku-4-5"

    # ── 에이전트 ────────────────────────────────────────────────────────────
    agent_max_iterations: int = 10
    # true → SupervisorExecutor (멀티 에이전트 + LangGraph OrderGraph)
    # false → 기존 단일 AgentExecutor
    use_multi_agent: bool = False

    # ── 외부 API ────────────────────────────────────────────────────────────
    anniversary_api_key: str = ""

    # ── 경로 ────────────────────────────────────────────────────────────────
    # 정책 문서(PDF/DOCX) 폴더. 기본값: shopping_mall/backend/ai/docs/
    # 다른 위치를 쓰려면 .env에 POLICY_DOCS_DIR=/절대/경로 로 지정.
    policy_docs_dir: str = str(BACKEND_ROOT / "ai" / "docs")

    @property
    def langgraph_postgres_url(self) -> str:
        """AsyncPostgresSaver(psycopg3)용 URL — SQLAlchemy 드라이버 접미사 제거.

        예: postgresql+asyncpg://... → postgresql://...
        PostgreSQL URL이 아닌 경우 ValueError를 발생시킵니다.
        """
        url = self.database_url
        if not re.match(r"^postgres(?:ql)?(?:\+\w+)?://", url):
            scheme_match = re.match(r"^([^:/]+)", url)
            detected = scheme_match.group(1) if scheme_match else "(unknown)"
            raise ValueError(
                f"DATABASE_URL이 PostgreSQL URL이 아닙니다 (감지된 스킴: {detected!r}). "
                "AsyncPostgresSaver는 PostgreSQL 전용입니다."
            )
        return re.sub(r"^(postgres(?:ql)?)\+\w+://", r"\1://", url)

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
