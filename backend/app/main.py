import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, Path as _PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.services.ai_agent_bridge import AiAgentBridge
from app.mcp import build_review_mcp

from app.api import (
    ai_agent,
    auth,
    farm_agent,
    health,
    journal,
    daily_journal,
    knowledge,
    market,
    pesticide,
    review_analysis,
    diagnosis,
    subsidy,
    weather,
)
from app.core.config import settings
from app.core.deps import get_current_user as _get_current_user_for_uploads
from app.core.database import async_session, close_db, init_db
from app.core.security import hash_password
from app.models.user import User  # noqa: F401 — Base.metadata 등록용
from app.models.review_analysis import ReviewAnalysis, ReviewSentiment  # noqa: F401
from app.models.diagnosis import DiagnosisHistory  # noqa: F401
from app.models.journal import JournalEntry  # noqa: F401
from app.models.daily_journal import DailyJournal, DailyJournalRevision  # noqa: F401
from app.models.ai_agent import (  # noqa: F401 — Base.metadata 등록용 (agent-action-history)
    AiAgentDecision,
    AiAgentActivityDaily,
    AiAgentActivityHourly,
)
from app.models.subsidy import Subsidy  # noqa: F401


async def seed_users():
    """테스트 계정이 없으면 시딩."""
    seed_data = [
        {
            "id": "farmer01",
            "name": "김사과",
            "email": "farmer01@farmos.kr",
            "password": "farm1234",
            "location": "경북 영주시",
            "area": 33.0,
            "farmname": "김사과 사과농장",
            "profile": "",
        },
        {
            "id": "parkpear",
            "name": "박배나무",
            "email": "parkpear@farmos.kr",
            "password": "pear5678",
            "location": "충남 천안시",
            "area": 25.5,
            "farmname": "박씨네 배 과수원",
            "profile": "",
        },
    ]
    async with async_session() as db:
        for data in seed_data:
            exists = await db.execute(select(User).where(User.id == data["id"]))
            if exists.scalar_one_or_none():
                continue
            user = User(
                id=data["id"],
                name=data["name"],
                email=data["email"],
                password=hash_password(data["password"]),
                location=data["location"],
                area=data["area"],
                farmname=data["farmname"],
                profile=data["profile"],
            )
            db.add(user)
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_users()

    # journal-entry-photos: 24h 경과 entry_id=null 인 사진 정리 (boot-time)
    try:
        from app.core.photo_storage import cleanup_orphans

        async with async_session() as db:
            removed = await cleanup_orphans(db, older_than_hours=24)
            if removed:
                logging.getLogger(__name__).info(
                    "journal_photo_orphans.cleaned count=%d", removed
                )
    except Exception:  # noqa: BLE001 — cleanup 실패가 기동 막지 않음
        # exception() 사용 → traceback 포함 자동 기록. 운영자가 즉시 원인 추적 가능.
        logging.getLogger(__name__).exception(
            "journal_photo_orphans.cleanup_failed"
        )

    # AI Agent Bridge (agent-action-history) — Relay patch 적용 시 활성화.
    # IOT_RELAY_API_KEY 가 비어 있으면 (env 미주입) 플래그가 켜져도 안전 비활성화한다.
    bridge: AiAgentBridge | None = None
    if settings.AI_AGENT_BRIDGE_ENABLED:
        if not settings.IOT_RELAY_API_KEY:
            logging.getLogger(__name__).warning(
                "ai_agent_bridge.disabled_missing_api_key "
                "AI_AGENT_BRIDGE_ENABLED=True 이지만 IOT_RELAY_API_KEY 가 비어있음 — "
                "환경변수/.env 로 키를 주입한 뒤 재시작하세요."
            )
        else:
            try:
                bridge = AiAgentBridge(settings=settings, session_factory=async_session)
                await bridge.start()
                app.state.ai_agent_bridge = bridge
            except Exception as exc:  # noqa: BLE001 — Bridge 실패가 BE 기동 막지 않음
                logging.getLogger(__name__).warning(
                    "ai_agent_bridge.start_failed err=%s", exc
                )

    # 공익직불 지원금 시드 (3개 프로그램)
    from app.services.subsidy.seed_data import seed_subsidies
    async with async_session() as db:
        await seed_subsidies(db)

    # 공익직불 RAG 준비 (팀원 신규 셋업 시 자동 인덱싱 포함):
    #  1) RAG 싱글톤 + 리랭커 모델 사전 로드 → 첫 요청 지연 방지
    #  2) ChromaDB 가 비어있고 Markdown 캐시가 있으면 자동 인덱싱
    #     (Git 리포에 커밋된 data/gov/*.md 를 사용 — PDF 재파싱 불필요)
    # 실패 시 서버 기동은 계속 (graceful fallback): /subsidy/match 는 DB 전용이므로 영향 없고,
    # /subsidy/ask 는 빈 citation + escalation_needed=True 로 응답.
    # 다만 관찰 가능성을 위해 app.state 에 플래그를 남겨 /health 등에서 확인 가능하게 함.
    import asyncio as _asyncio
    import logging as _logging
    _log = _logging.getLogger("app.main")
    app.state.subsidy_rag_ready = False
    try:
        from app.services.subsidy.gov_rag import _get_reranker, run_ingest_pipeline
        from app.services.subsidy.tools import _get_rag

        rag = await _asyncio.to_thread(_get_rag)
        if rag.count() == 0:
            _log.info("공익직불 ChromaDB 비어있음 — 캐시된 Markdown 으로 자동 인덱싱 시작")
            try:
                await _asyncio.to_thread(run_ingest_pipeline, False)
            except FileNotFoundError as e:
                _log.warning(f"자동 인덱싱 스킵 (Markdown 캐시 없음): {e}")
            except Exception as e:
                _log.error(f"자동 인덱싱 실패 — /subsidy/ask 는 빈 결과 반환: {e}", exc_info=True)
        await _asyncio.to_thread(_get_reranker)
        # Hybrid retrieval 의 sparse 절반 — BM25 인덱스를 미리 빌드해 첫 요청 지연 방지
        if rag.count() > 0:
            await _asyncio.to_thread(rag._ensure_bm25_built)
        app.state.subsidy_rag_ready = rag.count() > 0
    except Exception as e:
        # UPSTAGE_API_KEY 미설정·네트워크 오류·모델 로드 실패 등 — 서버 기동은 계속
        _log.warning(f"공익직불 RAG 준비 실패 (/subsidy/ask 제한 동작): {e}")

    # 💡 3일 지난 이미지를 삭제하는 백그라운드 태스크 시작
    # 헬스 가시성 향상: 연속 실패 카운터 + asyncio.to_thread 로 I/O 비동기화.
    app.state.cleanup_consecutive_failures = 0
    app.state.cleanup_total_deleted = 0
    _CLEANUP_FAIL_THRESHOLD = 5  # 5회 연속 실패 시 ERROR 레벨로 escalate

    async def cleanup_old_diagnosis_images():
        """24시간마다 data/uploads/diagnosis 디렉토리 내의 3일 이상 된 파일을 삭제."""
        upload_dir = Path(settings.UPLOAD_BASE_DIR) / "diagnosis"
        while True:
            try:
                if upload_dir.exists():
                    now = time.time()
                    three_days_ago = now - (3 * 24 * 60 * 60)

                    # 블로킹 I/O는 thread pool로 오프로드 — event loop 정체 방지
                    def _scan_and_delete(dir_path: Path, threshold: float) -> int:
                        deleted = 0
                        for file in dir_path.glob("*.webp"):
                            if file.is_file() and file.stat().st_mtime < threshold:
                                file.unlink()
                                deleted += 1
                        return deleted

                    deleted_count = await _asyncio.to_thread(
                        _scan_and_delete, upload_dir, three_days_ago,
                    )

                    if deleted_count > 0:
                        _log.info("Cleanup: Deleted %d old diagnosis images.", deleted_count)
                        app.state.cleanup_total_deleted += deleted_count

                # 정상 사이클 — 연속 실패 카운터 리셋
                app.state.cleanup_consecutive_failures = 0
            except Exception as e:  # noqa: BLE001 — 단일 실패가 task를 죽이지 않게
                app.state.cleanup_consecutive_failures += 1
                if app.state.cleanup_consecutive_failures >= _CLEANUP_FAIL_THRESHOLD:
                    # 연속 실패 임계 초과 — ERROR 로 격상해 모니터링 알림 트리거 가능
                    _log.error(
                        "cleanup.persistent_failure consecutive=%d err=%s "
                        "(disk full / permission denied 가능성)",
                        app.state.cleanup_consecutive_failures, e, exc_info=True,
                    )
                else:
                    _log.warning("cleanup.transient_failure err=%s", e)

            # 24시간 대기
            await _asyncio.sleep(24 * 60 * 60)

    # 💡 가비지 컬렉션 방지를 위해 app.state에 강한 참조 유지
    app.state.cleanup_task = _asyncio.create_task(cleanup_old_diagnosis_images())

    # 농약 DB 자동 시드 — 번들 VERSION 이 DB 버전보다 새로우면 백그라운드 시드
    try:
        from app.core.pesticide_autoseed import schedule_pesticide_autoseed
        schedule_pesticide_autoseed()
    except Exception as e:
        _log.warning(f"농약 DB 자동 시드 스케줄 실패: {e}")

    # ── LangSmith 트레이싱 환경변수 주입 ────────────────────────────────────
    # LangChain은 LANGCHAIN_* 환경변수를 직접 읽으므로 Settings 값을 os.environ에 주입.
    # API 키가 비어 있으면 비활성 (조용히 무시됨).
    if settings.LANGCHAIN_API_KEY:
        import os as _os
        _os.environ.setdefault("LANGCHAIN_TRACING_V2", settings.LANGCHAIN_TRACING_V2)
        _os.environ.setdefault("LANGCHAIN_API_KEY", settings.LANGCHAIN_API_KEY)
        _os.environ.setdefault("LANGCHAIN_PROJECT", settings.LANGCHAIN_PROJECT)
        _os.environ.setdefault("LANGCHAIN_ENDPOINT", settings.LANGCHAIN_ENDPOINT)
        _log.info("langsmith.enabled project=%s", settings.LANGCHAIN_PROJECT)

    # ── Farm Agent 브리핑 캐시 테이블 보장 ────────────────────────────────
    app.state.briefing_ready = False
    try:
        from app.services.farm_agent.briefing import (
            ensure_briefing_table,
            purge_cache_if_prompt_changed,
        )
        await ensure_briefing_table()
        await purge_cache_if_prompt_changed()
        app.state.briefing_ready = True
    except Exception as exc:  # noqa: BLE001 — 브리핑 캐시 실패는 서버 기동 막지 않음
        _log.warning("briefing.table_init_failed err=%s", exc)

    # ── ReasoningBank trajectory 테이블 ──────────────────────────────────
    try:
        from app.services.farm_agent.reasoning_bank import ensure_trajectory_table
        await ensure_trajectory_table()
    except Exception as exc:  # noqa: BLE001
        _log.warning("trajectory.table_init_failed err=%s", exc)

    # ── FarmOS Deep Agent (LangChain Deep Agents v0.5+) ───────────────────
    # Gemma 4-31B-IT 오케스트레이터 + 4 서브에이전트(diagnosis/subsidy/farm-data/verifier).
    # AsyncPostgresSaver(psycopg3 pool)로 thread별 멀티턴 메모리 영속화.
    # MCP 도구(외부 서버)는 lifespan에서 1회 로드 → 오케스트레이터 + farm-data-agent에 주입.
    # MemoryMiddleware로 backend/memory/AGENTS.md를 시스템 프롬프트에 자동 주입 (도메인 지식).
    # 초기화 실패 시 서버 기동은 계속 (graceful — /farm-agent/* 만 503).
    app.state.farm_agent = None
    app.state.farm_agent_pool = None
    app.state.farm_agent_checkpointer = None
    app.state.farm_agent_mcp_client = None
    app.state.mcp_server = None
    app.state.farm_agent_ready = False  # /health에서 노출하기 위한 플래그
    try:
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from sqlalchemy.engine.url import make_url

        from app.services.farm_agent import build_farm_agent
        from app.services.farm_agent.mcp_client import load_mcp_tools

        # SQLAlchemy URL → psycopg3 DSN 안전 변환 (str.replace 의 잠재적 비밀번호 손상 방지).
        # 비밀번호에 "+asyncpg" 같은 substring 이 포함된 극단 케이스에서도 안전.
        url = make_url(settings.DATABASE_URL).set(drivername="postgresql")
        pg_dsn = url.render_as_string(hide_password=False)

        pool = AsyncConnectionPool(
            conninfo=pg_dsn,
            max_size=10,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=False,
        )
        await pool.open()
        # 부분 초기화 실패 시에도 shutdown 핸들러가 풀을 닫을 수 있도록 즉시 등록.
        # 과거에는 success path 끝에서 등록되어 setup()/load_mcp_tools()/build_farm_agent()
        # 중 어느 단계라도 raise 하면 풀이 영구히 leak 되었다.
        app.state.farm_agent_pool = pool

        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()  # idempotent: 이미 있으면 no-op
        app.state.farm_agent_checkpointer = checkpointer

        # 외부 MCP 서버에서 도구 로드 (실패하면 빈 리스트로 graceful)
        mcp_tools, mcp_client = await load_mcp_tools(settings.MCP_SERVERS_JSON)
        app.state.farm_agent_mcp_client = mcp_client

        app.state.farm_agent = build_farm_agent(
            checkpointer=checkpointer,
            mcp_tools=mcp_tools,
        )
        app.state.farm_agent_ready = True
        _log.info(
            "farm_agent.initialized model=%s mcp_tools=%d",
            settings.LITELLM_MODEL,
            len(mcp_tools),
        )
    except Exception as exc:  # noqa: BLE001 — 에이전트 실패가 BE 기동 막지 않음
        _log.warning("farm_agent.init_failed err=%s", exc, exc_info=True)

    # ── 07:00 KST 브리핑 사전 생성 스케줄러 ───────────────────────────────
    # 매일 07:00 KST 에 모든 활성 사용자 브리핑을 미리 생성·캐시 → 사용자가 앱 열 때
    # on-demand LLM 대기 없이 즉시 응답. 캐시 미스 시에는 기존 폴백(on-demand 생성)
    # 이 동작하므로 스케줄러 실패가 사용자 경험을 깨지 않는다.
    app.state.briefing_scheduler_task = None
    if app.state.farm_agent_ready:
        try:
            from app.services.farm_agent.scheduler import run_briefing_scheduler

            app.state.briefing_scheduler_task = _asyncio.create_task(
                run_briefing_scheduler(app)
            )
        except Exception as exc:  # noqa: BLE001 — 스케줄러 실패가 BE 기동 막지 않음
            _log.warning("briefing.scheduler.start_failed err=%s", exc, exc_info=True)

    # ── MCP 서버 마운트 (lifespan 안에서 실행 — farm_agent 초기화 이후) ───────
    # 과거: 모듈 import 시점에 mount() 호출 → lifespan 시작 전에 동작 → app.state.farm_agent
    # 가 아직 None 인 상태에서 MCP 클라이언트 요청이 들어와 503 반복.
    # 현재: farm_agent 초기화 직후 마운트해 race window 제거.
    if settings.MCP_SERVER_ENABLED:
        try:
            from fastapi_mcp import FastApiMCP

            _mcp_safe_tag_allowlist = [
                "subsidy", "market", "weather", "knowledge", "diagnosis",
                "review-analysis", "pesticide", "journal", "daily_journal",
                "farm-agent",
            ]
            mcp_server = FastApiMCP(
                app,
                name=settings.MCP_SERVER_NAME,
                description="FarmOS 농업 데이터·진단·지원금 MCP 서버 (읽기 전용 + 에이전트 대화)",
                include_tags=_mcp_safe_tag_allowlist,
            )
            mcp_server.mount()
            app.state.mcp_server = mcp_server
            _log.info(
                "mcp_server.mounted name=%s path=/mcp tags=%s",
                settings.MCP_SERVER_NAME, sorted(_mcp_safe_tag_allowlist),
            )
        except Exception as exc:  # noqa: BLE001 — MCP 서버 실패가 BE 기동 막지 않음
            _log.warning("mcp_server.mount_failed err=%s", exc, exc_info=True)

    yield

    # 💡 백그라운드 태스크 종료 처리
    cleanup_task = getattr(app.state, "cleanup_task", None)
    if cleanup_task is not None:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except (_asyncio.CancelledError, Exception):
            pass

    briefing_scheduler_task = getattr(app.state, "briefing_scheduler_task", None)
    if briefing_scheduler_task is not None:
        briefing_scheduler_task.cancel()
        try:
            await briefing_scheduler_task
        except (_asyncio.CancelledError, Exception):
            pass

    if bridge is not None:
        try:
            await bridge.stop()
        except Exception as exc:  # noqa: BLE001 — 종료 실패도 BE shutdown 은 계속 진행
            logging.getLogger(__name__).warning(
                "ai_agent_bridge.stop_failed err=%s", exc, exc_info=True
            )

    # MCP 외부 클라이언트 정리 — TCP/SSE 커넥션 leak 방지
    mcp_client = getattr(app.state, "farm_agent_mcp_client", None)
    if mcp_client is not None:
        try:
            # MultiServerMCPClient 의 close 메서드 (버전마다 이름 다를 수 있음)
            close_fn = getattr(mcp_client, "aclose", None) or getattr(mcp_client, "close", None)
            if close_fn is not None:
                result = close_fn()
                if hasattr(result, "__await__"):
                    await result
        except Exception as exc:  # noqa: BLE001 — shutdown 흐름은 계속
            logging.getLogger(__name__).warning(
                "farm_agent.mcp_client_close_failed err=%s", exc, exc_info=True
            )

    farm_agent_pool = getattr(app.state, "farm_agent_pool", None)
    if farm_agent_pool is not None:
        try:
            await farm_agent_pool.close()
        except Exception as exc:  # noqa: BLE001 — shutdown 흐름은 계속
            logging.getLogger(__name__).warning(
                "farm_agent.pool_close_failed err=%s", exc, exc_info=True
            )
    await close_db()


# ---------------------------------------------------------------------------
# FastMCP 서버 — iot-review-mcp Design §6.1, §6.2
# ---------------------------------------------------------------------------
# review 분석/검색/리포트 함수들을 MCP tool 로 노출하는 sub-app.
# 같은 프로세스에 mount 하므로 core 싱글턴(_rag, _analyzer 등) 을 공유하고,
# 기존 JWT 검증 로직(core.security.decode_access_token) 도 그대로 재사용한다.
# Lifespan 통합은 fastmcp.utilities.lifespan.combine_lifespans 사용.
#
# Graceful fallback: MCP 빌드/http_app 실패 시 _review_mcp_app=None 으로 폴백.
# Bridge·RAG·사진 정리와 동일 패턴 — MCP 만 비활성화되고 REST 본체는 정상 기동.
# stateless_http=True: 단발 HTTP 호출도 세션 핸드셰이크 없이 동작 (T4 progress SSE 영향 없음).
_review_mcp_app = None
try:
    _review_mcp = build_review_mcp()
    _review_mcp_app = _review_mcp.http_app(path="/", stateless_http=True)
except Exception as _mcp_exc:  # noqa: BLE001 — MCP 실패가 BE 기동 막지 않음
    logging.getLogger(__name__).exception(
        "review_mcp.build_failed — /mcp 비활성, REST 본체는 정상 기동 (err=%s)",
        _mcp_exc,
    )

if _review_mcp_app is not None:
    try:
        from fastmcp.utilities.lifespan import combine_lifespans
        _combined_lifespan = combine_lifespans(lifespan, _review_mcp_app.lifespan)
    except ImportError:
        # combine_lifespans 가 없는 fastmcp 버전일 때의 폴백.
        # 두 lifespan 을 단일 async with 로 합성 (PEP 617 multi-context — enter A→B, exit B→A).
        @asynccontextmanager
        async def _combined_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # type: ignore[no-redef]
            async with lifespan(app), _review_mcp_app.lifespan(app):
                yield
else:
    _combined_lifespan = lifespan


app = FastAPI(title=settings.PROJECT_NAME, lifespan=_combined_lifespan)
# /health 등에서 MCP 가용성 확인용 플래그 (관찰성).
app.state.review_mcp_available = _review_mcp_app is not None

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    # CORS spec: allow_credentials=True 와 allow_methods/headers="*" 조합은 모던 브라우저가
    # 거부하므로 명시적 화이트리스트로 핀(pin) — 보안과 호환성을 동시에 확보.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Accept",
        "Accept-Language",
        "Authorization",
        "Content-Type",
        "Cookie",
        "Origin",
        "X-Requested-With",
    ],
)

# 업로드 정적 파일 — JWT 쿠키 인증 게이트 적용
# 과거: StaticFiles 가 인증 없이 마운트되어 진단 이미지(농장 위치·작물 상태 등 PII 가능)가
# UUID URL 만 알면 누구나 접근 가능한 상태였다.
# 현재: FastAPI 라우트로 감싸 get_current_user 통과한 사용자만 파일 다운로드 가능.
# 경로 traversal 방어: 정규식 검증 + Path.resolve() relative_to() 검증 (이중 방어).
_UPLOAD_ROOT = Path(settings.UPLOAD_BASE_DIR).resolve()


@app.get("/uploads/{subdir}/{filename}", include_in_schema=False)
async def serve_upload(
    subdir: str = _PathParam(..., pattern=r"^[a-zA-Z0-9_-]+$"),
    filename: str = _PathParam(..., pattern=r"^[a-zA-Z0-9_.-]+$"),
    _user=Depends(_get_current_user_for_uploads),  # noqa: B008 — FastAPI Depends 패턴
):
    """인증된 사용자에게만 업로드 파일 서빙 (path traversal 방어 + auth)."""
    candidate = (_UPLOAD_ROOT / subdir / filename).resolve()
    # candidate 가 _UPLOAD_ROOT 밖을 가리키면 거부
    try:
        candidate.relative_to(_UPLOAD_ROOT)
    except ValueError:
        raise HTTPException(status_code=403, detail="잘못된 경로입니다.")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    return FileResponse(candidate)

app.include_router(auth.router, prefix=settings.API_V1_PREFIX)
app.include_router(health.router, prefix=settings.API_V1_PREFIX)
app.include_router(journal.router, prefix=settings.API_V1_PREFIX)
app.include_router(daily_journal.router, prefix=settings.API_V1_PREFIX)
app.include_router(knowledge.router, prefix=settings.API_V1_PREFIX)
app.include_router(pesticide.router, prefix=settings.API_V1_PREFIX)
app.include_router(market.router, prefix=settings.API_V1_PREFIX)
app.include_router(weather.router, prefix=settings.API_V1_PREFIX)
app.include_router(review_analysis.router, prefix=settings.API_V1_PREFIX)
app.include_router(diagnosis.router, prefix=settings.API_V1_PREFIX)
app.include_router(ai_agent.router, prefix=settings.API_V1_PREFIX)
app.include_router(subsidy.router, prefix=settings.API_V1_PREFIX)
app.include_router(farm_agent.router, prefix=settings.API_V1_PREFIX)

# MCP sub-app mount (Design §6.1) — POST /mcp/ 에 streamable-http endpoint 노출.
# 빌드 실패 시 (_review_mcp_app=None) mount 생략 — REST 본체는 그대로 동작.
# Note: FastApiMCP 서버 마운트는 lifespan 내부 (farm_agent 초기화 직후) 에서 처리한다.
# 과거 모듈 import 타임 마운트는 lifespan 시작 전에 동작해 race condition 의 원인이었다.
if _review_mcp_app is not None:
    app.mount("/mcp", _review_mcp_app)
