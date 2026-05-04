from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


# 프로젝트 번들 폰트 경로 — cross-platform 기본값.
# `.env` 의 FONT_PATH / FONT_BOLD_PATH 로 개별 오버라이드 가능.
_BUNDLED_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


class Settings(BaseSettings):
    # ── 기본 설정 ──────────────────────────────────────────────────────────
    PROJECT_NAME: str = ""
    API_V1_PREFIX: str = ""
    APP_TIMEZONE: str = ""

    # 개발 모드 — True 일 때 farm_agent SSE 가 실제 예외 메시지를 사용자에게
    # 그대로 노출한다 (디버깅 편의). 운영에서는 반드시 .env 에서 false 로.
    DEBUG: bool = False

    # ── 데이터베이스 ────────────────────────────────────────────────────────
    # 데이터베이스 (PostgreSQL)
    DATABASE_URL: str = ""
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800

    # 벡터 데이터베이스 (ChromaDB)
    CHROMA_DB_PATH: str = ""

    # ── 보안 및 인증 ────────────────────────────────────────────────────────
    # JWT 시크릿 키 (FarmOS-ShoppingMall 공유 인증)
    JWT_SECRET_KEY: str = ""

    # 쿠키 보안 (운영 = True / 로컬 HTTP = False)
    # secure=True 면 HTTPS 응답에서만 브라우저가 쿠키 전송한다.
    # 프로덕션 배포 시 반드시 .env 에서 COOKIE_SECURE=true 로 오버라이드.
    COOKIE_SECURE: bool = False

    # 비밀번호 재설정 이메일 발신 토글
    # False (기본): /find-password 응답에 reset_token 포함하지 않고 200만 반환 → 이메일 채널 미구축 환경에서도 토큰 노출 차단
    # True: 이메일 발송 인프라가 구비된 경우 SMTP 연동 (별도 구현 필요).
    PASSWORD_RESET_EMAIL_ENABLED: bool = False

    # CORS 허용 도메인 (JSON 배열 형식)
    # 프론트엔드 → 백엔드 API 호출 허용
    CORS_ORIGINS: list[str] = []

    # ── 외부 API (공공데이터/지도) ──────────────────────────────────────────
    # 기상청 API (단기예보 등)
    # 기상청 단기예보 서비스 (지역 날씨 오케스트레이션용)
    KMA_DECODING_KEY: str = ""

    # 국가농작물병해충관리시스템 (NCPMS)
    NCPMS_API_KEY: str = ""

    # 농약안전정보시스템 
    PESTICIDE_API_KEY: str = ""

    # 식품안전나라
    # 식품의약품안전처 공공데이터활용 — 농약 등록정보(I1910) 조회에 사용
    # 회원가입 후 Open-API 이용신청 → 인증키 발급
    FOOD_SAFETY_API_KEY: str = ""

    # 농산물유통정보 (KAMIS)
    KAMIS_API_KEY: str = ""
    KAMIS_CERT_ID: str = ""

    # 카카오 REST API (위도 및 경도 변환)
    KAKAO_REST_API_KEY: str = ""

    # ── LLM & AI 서비스 ──────────────────────────────────────────────────────
    # LiteLLM 프록시 / OpenRouter
    # LiteLLM 사용 모델 목록 -> gpt-5-nano, gpt-5-mini, gpt-oss-20b, gemma-4-31b-it
    LITELLM_API_KEY: str = ""
    LITELLM_URL: str = ""
    LITELLM_MODEL: str = ""

    # OpenRouter (Farm Agent 전용 — Grok 4.1 Fast with reasoning)
    # 비어있으면 LITELLM_* 로 폴백한다.
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_MODEL: str = "x-ai/grok-4.1-fast"
    # 기본 OFF — reasoning ON 은 LLM 호출당 8-25 초 추가. Deep Agents 는 한 턴에
    # LLM 을 3-4 회 호출하므로 (오케스트레이터 → 서브에이전트 → 합성) 차이가 누적되어
    # 30-70 초 vs 6-14 초 로 5x 차이가 난다. 어려운 질의에만 ON 으로 토글.
    OPENROUTER_REASONING_ENABLED: bool = False

    # Groq (Whisper STT)
    # 영농일지 서버사이드 음성 전사(STT)에 사용
    GROQ_API_KEY: str = ""
    GROQ_STT_URL: str = ""
    GROQ_STT_MODEL: str = ""

    # 리뷰 분석 및 기타 LLM 설정
    LLM_PROVIDER: str = ""
    LLM_MODEL: str = ""
    OLLAMA_BASE_URL: str = ""
    OLLAMA_REMOTE_URL: str = ""

    EMBED_MODEL: str = ""
    EMBED_DIM: int = 1024

    # LLM 리즈닝 강도 (GPT-5 계열 reasoning 모델용)
    # minimal | low | medium | high  또는 "none"(파라미터 미전송)
    # non-reasoning 모델(gemma, gpt-oss 등)은 무시됨
    LLM_REASONING_EFFORT: str = ""

    # Review Embedding (LiteLLM 프록시 경유, VoyageAI 등)
    REVIEW_ANALYSIS_BATCH_SIZE: int = 40
    REVIEW_ANALYSIS_MAX_RETRIES: int = 2

    # 해충 이미지 분류 서버 (RunPod 에 배포된 pest-detector-deploy)
    # 예: https://<POD_ID>-<PORT>.proxy.runpod.net  (끝에 슬래시 없이)
    # 비워두면 /diagnosis/upload 가 pest 필드 없이 image_url 만 반환한다.
    PEST_CLASSIFIER_URL: str = ""

    # ── AI Agent (IoT 제어) ──────────────────────────────────────────────────
    AI_AGENT_MODEL: str = ""
    AI_AGENT_LLM_INTERVAL: int = 300
    AI_AGENT_RULE_INTERVAL: int = 30

    # IoT Relay Server Bridge
    # AI Agent Action History Bridge (Relay → FarmOS 미러)
    # Relay 와 공유하는 시크릿. 비워두면 AI_AGENT_BRIDGE_ENABLED=true 라도 안전 비활성화된다.
    # 운영 환경에서는 절대 코드에 하드코딩하지 말고 반드시 환경변수/.env 로만 주입한다.
    # 실제 키는 반드시 .env / 환경변수(IOT_RELAY_API_KEY) 로 주입한다.
    # 빈 문자열이면 AI_AGENT_BRIDGE_ENABLED=True 라도 Bridge 는 안전하게 비활성화된다.
    IOT_RELAY_BASE_URL: str = ""
    IOT_RELAY_API_KEY: str = ""
    AI_AGENT_BRIDGE_ENABLED: bool = False
    AI_AGENT_MIRROR_TTL_DAYS: int = 30
    AI_AGENT_BACKFILL_PAGE_SIZE: int = 200

    # 센서 임계값
    SOIL_MOISTURE_LOW: float = 55.0
    SOIL_MOISTURE_HIGH: float = 70.0

    # ── 기타 설정 ────────────────────────────────────────────────────────────
    # 업로드 경로 (절대 경로 전환을 위한 베이스)
    UPLOAD_BASE_DIR: str = "data/uploads"

    # 농장 위치 (기상청 격자좌표 기본값)
    FARM_NX: int = 84
    FARM_NY: int = 106

    # 기상청 중기예보 지역 코드 (5일 이상 예보용).
    # MID_LAND_REG_ID: 육상예보권역 코드 — 11B00000(서울·경기), 11D10000(영동),
    #   11D20000(영서), 11C20000(대전·세종·충남), 11C10000(충북),
    #   11F10000(광주·전남), 11F20000(전북), 11H10000(대구·경북),
    #   11H20000(부산·울산·경남), 11G00000(제주)
    # MID_TEMP_REG_ID: 시군 단위 코드. 영주=11H10401, 서울=11B10101 등.
    # 자세한 매핑은 공공데이터포털 기상청 중기예보 API 문서 참고.
    KMA_MID_LAND_REG_ID: str = "11H10000"
    KMA_MID_TEMP_REG_ID: str = "11H10401"

    # 한글 폰트 (PDF 생성용) — 저장소에 번들된 Pretendard(SIL OFL 1.1) 기본 사용.
    # 시스템 폰트 사용하려면 .env에서 절대 경로로 오버라이드 가능.
    FONT_PATH: str = str(_BUNDLED_FONTS_DIR / "Pretendard-Regular.ttf")
    FONT_BOLD_PATH: str = str(_BUNDLED_FONTS_DIR / "Pretendard-Bold.ttf")

    # ── Farm Agent (FarmOS Deep Agent) ──────────────────────────────────────
    # Fast-path: 단순 질의(날씨/시세/일지/IoT 이력)를 LLM 우회로 즉시 응답.
    # 안전 민감 키워드(농약/직불/진단 등)는 자동으로 fast-path 거부.
    FARM_AGENT_FAST_PATH_ENABLED: bool = True
    # 입력 길이 제한 — fast-path 매칭 시 너무 긴 질의는 복잡한 의도로 보고 정상 흐름으로 위임
    FARM_AGENT_FAST_PATH_MAX_LEN: int = 80
    # SSE 스트리밍 heartbeat 주기 (초). nginx 등 리버스 프록시의 idle timeout 회피.
    # Gemma 가 task 위임 직후 첫 토큰까지 10초 이상 걸릴 수 있어 heartbeat 필요.
    FARM_AGENT_SSE_HEARTBEAT_SEC: int = 15

    # 직불(시행지침) 답변에 [doc > 제N조] 인용이 누락되면 1회 재프롬프트.
    # ReasoningBank 패턴: 실패한 turn 을 graph 내부에서 즉시 보정한다.
    # OFF 로 두면 iter-4 의 low_confidence 경고 신호만 유지 (재프롬프트 없음).
    FARM_AGENT_CITATION_REPROMPT_ENABLED: bool = True
    # 재프롬프트 최대 횟수 — 1 이 권장. 2 이상은 latency 폭발 위험.
    FARM_AGENT_CITATION_REPROMPT_MAX: int = 1

    # 추가 메모리 파일 (CSV, backend/ 기준 상대경로 또는 절대경로).
    # 기본 AGENTS.md (도메인 상수) 외에 ReasoningBank 스타일 STRATEGIES.md
    # (전략-수준 추론 힌트 + 실패 모드 회피 패턴) 등을 추가 주입한다.
    # 예: "memory/STRATEGIES.md,memory/POLICIES.md"
    # 비어 있으면 AGENTS.md 만 사용 (기존 동작 유지).
    FARM_AGENT_MEMORY_PATHS: str = "memory/STRATEGIES.md"

    # ── Redis LangCache (LLM 응답 의미 기반 캐시) ───────────────────────────
    # Hosted semantic cache — 직불/진단/시세 등 반복·유사 질의의 LLM 호출을 우회한다.
    # 캐시 hit 시 LLM 라운드트립(2-10s)을 ms 단위 lookup 으로 대체 → 비용·지연 동시 감소.
    # 비활성: LANGCACHE_API_KEY / CACHE_ID / SERVER_URL 중 하나라도 비어있으면 자동 OFF.
    # Scope: 답변은 사용자별 attributes={"user_id": ...} 로 격리 — 농장 컨텍스트 누수 방지.
    LANGCACHE_API_KEY: str = ""
    LANGCACHE_CACHE_ID: str = ""
    LANGCACHE_SERVER_URL: str = "https://api.langcache.redis.io"
    # Threshold 0.0-1.0 — Redis 권장 시작값 0.85. 낮추면 paraphrase 더 잡고 오답 risk ↑.
    LANGCACHE_SIMILARITY_THRESHOLD: float = 0.85
    LANGCACHE_ENABLED: bool = True

    # LangSmith 트레이싱 (Deep Agent + 서브에이전트 + 도구 호출 전체 추적).
    # API 키가 비어 있으면 자동 비활성. shopping_mall과 동일 키 공유 가능.
    LANGCHAIN_TRACING_V2: str = "false"
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "farmos-deepagent"
    LANGCHAIN_ENDPOINT: str = "https://api.smith.langchain.com"

    # ── MCP (Model Context Protocol) ────────────────────────────────────────
    # 클라이언트 모드: FarmOS Deep Agent 가 외부 MCP 서버의 도구를 흡수.
    # JSON 형식 — 키는 서버명, 값은 langchain-mcp-adapters 의 server config.
    # 예: '{"weather":{"url":"http://localhost:9001/mcp","transport":"http"}}'
    # 비어있으면 외부 MCP 도구 없이 네이티브 서브에이전트만 동작.
    MCP_SERVERS_JSON: str = ""

    # 서버 모드: FarmOS REST 엔드포인트를 MCP 도구로 노출 (Claude/Cursor 클라이언트용).
    # /mcp 경로에 마운트. 기존 JWT 쿠키 인증 (get_current_user) 그대로 적용된다.
    MCP_SERVER_ENABLED: bool = False
    MCP_SERVER_NAME: str = "FarmOS"

    # 공익직불 RAG (정부 지원금 매칭) — subsidy-scoped, does not affect other modules
    UPSTAGE_API_KEY: str = ""
    # LLM URL/Key 는 저장소 공통의 LITELLM_URL / LITELLM_API_KEY 를 그대로 사용
    # (diagnosis·review 모듈과 동일 경로 → 팀 API 사용량 통합 추적)
    SUBSIDY_LLM_MODEL: str = "google/gemma-4-31b-it"
    SUBSIDY_RERANKER_MODEL: str = "dragonkue/bge-reranker-v2-m3-ko"
    SUBSIDY_PDF_PATH: str = "data/gov/2026_공익직불_시행지침.pdf"
    SUBSIDY_MARKDOWN_CACHE_PATH: str = "data/gov/2026_공익직불_시행지침.md"

    # ── 영농일지 Vision 입력 (사진 → AI 자동 작성) ─────────────────────────────
    # LiteLLM 프록시에 등록된 vision-capable 모델 ID. 기존 LITELLM_URL/LITELLM_API_KEY 재사용.
    # 2026-04-28 기준 프록시 등록 vision 모델: gpt-5-mini, gpt-5-nano (GPT-5 family).
    # Gemini 2.5 Flash 등 다른 모델 등록 시 .env 의 LITELLM_VISION_MODEL 으로 오버라이드.
    # 숫자 제한들은 Field 로 범위 검증해 startup 시점에 fail-fast (env 오입력 방어).
    LITELLM_VISION_MODEL: str = Field(default="gpt-5-mini", min_length=1)
    JOURNAL_VISION_TIMEOUT_S: float = Field(default=120.0, gt=0, le=300)
    JOURNAL_VISION_MAX_IMAGES: int = Field(default=10, ge=1, le=20)
    JOURNAL_VISION_MAX_BYTES: int = Field(
        default=5 * 1024 * 1024, ge=1, le=50 * 1024 * 1024
    )  # 1B ~ 50MB

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
