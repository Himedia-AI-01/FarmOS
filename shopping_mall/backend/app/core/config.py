"""애플리케이션 설정 — 환경변수를 한 곳에서 관리합니다."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── 데이터베이스 ────────────────────────────────────────────────────────
    database_url: str = ""

    # ── 인증 ────────────────────────────────────────────────────────────────
    jwt_secret_key: str = ""

    # ── Ollama (임베딩 전용) ─────────────────────────────────────────────────
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

    # ── 외부 API ────────────────────────────────────────────────────────────
    anniversary_api_key: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
