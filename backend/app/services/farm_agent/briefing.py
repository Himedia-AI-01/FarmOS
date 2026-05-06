"""Proactive daily briefing — 사용자별 아침 브리핑 자동 생성 (캐시 6h).

설계:
  - On-demand 생성 + Postgres 캐시. 매 사용자 사전 생성은 비용 과다.
  - thread_id = f"briefing:{user_id}" 로 stateful — 어제 브리핑을 참고해 오늘 차이만 강조.
  - 안전 민감 도메인(농약·자격 판정) 직접 답변 없이 "확인이 필요하면 별도 문의" 안내만.

브리핑 구성:
  1. 오늘 날씨 + 단기 예보
  2. 주요 작물(사용자 main_crop) 현재 시세
  3. 어제 IoT 자율 제어 이력 요약
  4. 영농일지 누락 알림 (어제 작업 기록 누락 시)
  5. 오늘 권장 작업 (날씨·작물 단계 기반 LLM 추론)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.database import Base, async_session

logger = logging.getLogger(__name__)

# 비어있거나 실패 표식인 응답을 캐시에 저장하지 않기 위한 최소 길이.
# 빈 응답이나 짧은 에러 문자열이 6시간 동안 사용자에게 반복 노출되는 캐시 포이즈닝 방지.
_MIN_VALID_CONTENT_LEN = 200


class FarmAgentBriefing(Base):
    """일일 브리핑 캐시.

    PK = (user_id, briefing_date). briefing_date 는 KST 자정 → UTC 변환된 timestamptz.
    날짜만 저장하지만 시간대 안정성을 위해 TIMESTAMPTZ를 사용한다 (Date로 저장 시
    클라이언트 타임존에 따라 하루가 어긋날 수 있음).
    """

    __tablename__ = "farm_agent_briefings"

    user_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    briefing_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


_BRIEFING_PROMPT = """\
당신은 농장 운영 전담 AI 비서입니다. 오늘 ({today}) 아침, 사장님이 하루를 시작할 때
가장 가치 있는 통찰과 권장을 담은 브리핑을 작성하세요.

## 정보 수집 (자율적으로 판단해 도구 활용)
필요한 도구를 직접 병렬 호출해 데이터를 모으세요. 다음이 보통 핵심 출처:
- `get_my_farm_profile` — 작물·지역·면적·재배 단계
- `get_current_weather` / `get_weather_risk_advisory` — 오늘 + 5일 위험 분석
- `get_recent_iot_decisions(hours=24)` — 어제 자율 제어 이력
- `get_journal_daily_summary("{yesterday}")` / `list_journal_entries` — 영농일지 흐름
- `get_market_prices_for_crop` — 주작물 시세 (의미있는 변동 시만 언급)
- `list_eligible_subsidies` — 신규 매칭 지원금이 있을 때만

질문 사이즈가 클수록 더 많이 모아도 좋습니다. 부족하면 추가 호출, 충분하면 합성.
task() 서브에이전트 위임은 본 모드에서 사용 금지 — 도구 직접 호출만.

## 브리핑 작성 (대화체 + 가벼운 마크다운)
사장님께 직접 말씀드리듯 자연스러운 한국어 문장으로 작성하세요. 1-2분 안에 읽을 분량.
다음 정보를 자연스럽게 녹여내세요:
- 오늘 가장 중요한 신호 1개
- 위험·주의 사항 (강수 ≥30%, 풍속 ≥3m/s, 기온 ≥30℃ 등 또는 작물 단계 위험)
- 오늘 날씨와 작업 가능 시간대 (구체 시간대로)
- 권장 작업 1-3개 — 각 작업의 근거 한 줄
- 어제 농장 상태와 의미있는 변화
- 시세는 변동 폭이 의미있을 때만 언급

## 형식 규칙 (중요 — 가독성)
- **문장 위주.** 표(테이블)·구분선(`---`)·과한 헤더 사용 금지.
- 헤더는 짧은 제목 한두 개로 충분합니다. 매 섹션마다 헤더 + 빈 줄 패턴 반복 금지.
- 이모지는 핵심 강조용 1-3개만. 모든 항목에 이모지 붙이지 말 것.
- 데이터 비교가 필요하면 표 대신 한 문장으로 풀어쓰세요.
  예시 좋음: "쌀 20kg 도매 60,420원으로 1년 전 대비 +17% 올랐습니다."
  예시 나쁨: 마크다운 표로 행 4개 출력.
- 단순한 날엔 짧게 (5-7문장), 이상 신호 있는 날엔 더 길게.

존댓말, "사장님" 호칭. 추측 금지 — 도구 미반환 값은 "확인 어려움".

## 안전 (절대)
- 농약 제품명·희석배수·살포 시기 추천 금지 — "전용 챗봇에 문의하세요" 안내.
- 직불금·자격 판정은 본 브리핑에서 단정 금지 — "공익직불 챗봇에 문의" 안내.
- 도구가 반환하지 않은 수치 임의로 보충 금지.

## 출력
- 마크다운 그대로 출력. 코드펜스(```) 금지.
- 메타 사고·자기 대화·계획 설명 절대 출력 금지.
"""


_BRIEFING_SYSTEM_PROMPT_BASE = """\
당신은 FarmOS 농장 운영 전담 AI 비서입니다. 일일 아침 브리핑 전용 모드.

사용자 메시지의 가이드를 따라 사장님께 가장 가치 있는 브리핑을 자율적으로 작성합니다.
구조·길이는 사용자 메시지가 권장하는 범위 내에서 농장 상황에 맞게 조정하세요.

## 도구 사용
필요한 도구를 직접 병렬 호출해 정보를 모으세요. task() 서브에이전트 위임은 사용 금지.
정보가 부족하면 추가 호출, 충분하면 합성.

## 도메인 지식 (한국 농민 50-70대 컨텍스트)
- 어휘는 초등 6학년 수준. 한자어는 괄호 풀이 (예: "방제(병해충 막기)").
- 호칭 "사장님", 존댓말. 추측 금지 — 도구 미반환 값은 "확인 어려움".
- 출처: 농약→농촌진흥청 / 시세→KAMIS / 날씨→기상청.
- 수치(면적·금액·희석배수)는 도구 결과 그대로. 변형·반올림 금지.
- 위험 신호 임계: 강수 ≥30%, 풍속 ≥3m/s, 기온 ≥30℃ — 해당 시 명확히 강조.
- 농약 제품명·희석배수·자격 판정은 브리핑에서 직접 답변 금지 → "전용 챗봇 문의" 안내.
- IoT 제어 명령 처리 안 함 (브리핑은 조회만).

## 출력 형식 (가독성 우선 — 강제 규칙)
- **대화체 문장 위주.** 사장님께 직접 말씀드리듯 풀어쓰세요.
- **표(마크다운 테이블) 절대 사용 금지** — 데이터 비교도 문장으로.
  ✗ `| 항목 | 값 |...|` 같은 표 출력 = 무효 응답.
  ✓ "쌀 20kg 도매가 60,420원, 1년 전보다 17% 올랐습니다." 같은 문장.
- **구분선(`---`) 절대 사용 금지**.
- **헤더 최소화** — 긴 브리핑에서 한두 개 핵심 헤더만. 매 섹션마다 ### 헤더 금지.
- **이모지 1-3개만** — 핵심 강조용. 모든 줄·문단에 이모지 붙이지 말 것.
- 코드펜스(```) 금지. 메타 사고·자기 대화·계획 설명 금지.
"""


# Cache for the resolved memory prompt — AGENTS.md doesn't change between
# requests, so synchronous file I/O on every request is wasted work. Cache
# is invalidated on app restart (good enough for "edit AGENTS.md → rerun").
_BRIEFING_PROMPT_CACHE: dict[str, str] = {}


def _briefing_system_prompt() -> str:
    """AGENTS.md 메모리 + 베이스 프롬프트 합성.

    오케스트레이터는 MemoryMiddleware 로 backend/memory/AGENTS.md 를 자동 주입하지만,
    직접 호출 ReAct agent 는 이 미들웨어를 거치지 않아 도메인 지식을 잃는다.
    여기서 명시적으로 합쳐 같은 컨텍스트를 갖게 한다 (파일 부재 시 graceful skip).

    File I/O는 첫 호출 시 1회만 — 이후 process-local cache 에서 즉시 반환. Async
    경로에서 호출되더라도 cache hit 시 disk I/O 없음 (event-loop block 회피).
    """
    cached = _BRIEFING_PROMPT_CACHE.get("prompt")
    if cached is not None:
        return cached

    base = _BRIEFING_SYSTEM_PROMPT_BASE
    try:
        from pathlib import Path

        # Anchor to backend/ — same root that agent.py uses (`parents[3]` from
        # backend/app/services/farm_agent/*.py both resolve to backend/).
        backend_root = Path(__file__).resolve().parents[3]
        memory_paths: list[Path] = [backend_root / "memory" / "AGENTS.md"]

        # FARM_AGENT_MEMORY_PATHS is operator-supplied; validate that each
        # entry resolves to a real file UNDER backend/ to prevent prompt
        # injection via traversal (`../../../etc/passwd`) or absolute paths
        # to attacker-controlled files. Mirrors agent.py:_resolve_memory_sources.
        extra = getattr(settings, "FARM_AGENT_MEMORY_PATHS", "") or ""
        for raw in [s.strip() for s in extra.split(",") if s.strip()]:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = backend_root / candidate
            if not candidate.exists():
                logger.warning("briefing.memory_missing path=%s — skipped", candidate)
                continue
            try:
                candidate.resolve().relative_to(backend_root)
            except ValueError:
                logger.warning(
                    "briefing.memory_outside_backend path=%s — skipped (must live under backend/)",
                    candidate,
                )
                continue
            memory_paths.append(candidate)

        chunks: list[str] = []
        for path in memory_paths:
            if path.is_file():
                chunks.append(path.read_text(encoding="utf-8"))
        if chunks:
            result = base + "\n\n## 추가 메모리 (자동 주입)\n\n" + "\n\n".join(chunks)
        else:
            result = base
    except Exception:  # noqa: BLE001 — 메모리 로드 실패 시 베이스만 사용
        logger.exception("briefing.memory_load_failed — base prompt only")
        result = base

    _BRIEFING_PROMPT_CACHE["prompt"] = result
    return result


# Direct-LLM 호출용 도구 화이트리스트 — 브리핑에 필요한 read-only 도구만 노출.
# 오케스트레이터의 task() 위임 도구를 제외해 routing 분기를 차단한다.
def _briefing_tools() -> list:
    """브리핑 직접 호출에서 사용할 도구 리스트 (lazy import — 순환 회피)."""
    from app.services.farm_agent.tools import (
        get_current_weather,
        get_journal_daily_summary,
        get_market_prices_for_crop,
        get_my_farm_profile,
        get_recent_iot_decisions,
        get_weather_risk_advisory,
        list_eligible_subsidies,
        list_journal_entries,
    )
    return [
        get_my_farm_profile,
        get_current_weather,
        get_weather_risk_advisory,
        get_recent_iot_decisions,
        get_journal_daily_summary,
        get_market_prices_for_crop,
        list_journal_entries,
        list_eligible_subsidies,
    ]


async def _generate_via_direct_llm(user_id: str, prompt: str) -> str:
    """오케스트레이터를 우회해 LLM + 도구만으로 브리핑 생성.

    배경: 오케스트레이터의 ORCHESTRATOR_PROMPT 가 시스템 메시지로 "2-4문장" 단답
    규칙을 강제해 브리핑의 7섹션·800자 요구를 무력화시킴. 본 모드는 브리핑 전용
    시스템 프롬프트로 LLM 을 직접 호출 — task() 위임 없이 도구만 병렬 호출한다.

    LangGraph create_react_agent 사용. recursion_limit 은 도구 8개 + 합성 1턴
    예상치의 2배(20) 로 설정.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from langgraph.prebuilt import create_react_agent

    from app.services.farm_agent.models import build_llm_for

    llm = build_llm_for("briefing", max_tokens=3072)
    react_agent = create_react_agent(
        llm,
        tools=_briefing_tools(),
        prompt=SystemMessage(content=_briefing_system_prompt()),
    )
    state = await react_agent.ainvoke(
        {"messages": [HumanMessage(content=prompt)]},
        config={
            "recursion_limit": 20,
            "configurable": {"user_id": user_id},
        },
    )
    messages = state.get("messages", [])
    if not messages:
        return ""
    return getattr(messages[-1], "content", "") or ""


# Per-(user, date) generation lock. Prevents two concurrent requests from both
# missing cache and both spending LLM tokens on the same briefing. The lock is
# acquired AFTER the cache miss and RE-CHECKED inside the lock — classic
# double-checked locking. Once the first holder finishes and writes the cache,
# subsequent waiters see the cached row and return without LLM cost.
#
# Process-local only: with multiple FastAPI workers this still allows N
# concurrent generations across processes. Acceptable trade-off vs needing a
# Redis/Postgres advisory lock; per-user same-second collision rate is low.
_GENERATION_LOCKS: dict[tuple[str, date], asyncio.Lock] = {}


def _get_or_create_lock(key: tuple[str, date]) -> asyncio.Lock:
    """Get or lazily create the per-(user, date) generation lock.

    Locks are kept in a module-level dict; not pruned because entries are
    keyed on (user_id, date) so a user accumulates at most ~365 entries/year —
    negligible memory footprint, and pruning would race with active waiters.
    """
    lock = _GENERATION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _GENERATION_LOCKS[key] = lock
    return lock


async def get_or_generate_briefing(
    agent,  # CompiledStateGraph
    user_id: str,
    user_name: str,
    target_date: date | None = None,
    *,
    force_regenerate: bool = False,
) -> tuple[str, bool]:
    """캐시된 오늘 브리핑이 있으면 반환, 없으면 에이전트로 생성 후 저장.

    Returns:
        (content, cached) — cached=True 면 DB 적중, False 면 LLM으로 새로 생성.
        과거 API 라우터가 `cached_before = not refresh` 로 항상 True 를 응답해
        클라이언트에 거짓 신호를 보내던 버그를 함수 레벨에서 정직하게 보고하도록 수정.

    동시성:
        같은 (user, date) 로 두 요청이 동시에 와도 LLM 호출은 한 번만. 첫 번째가
        락 안에서 생성·캐시 → 두 번째는 락 진입 후 cache hit 으로 반환.

    Cache poisoning 방지:
        에이전트가 빈 응답이나 짧은 실패 메시지를 반환하면 DB에 저장하지 않는다.
        (과거: "브리핑을 생성하지 못했습니다" 가 6시간 캐시되어 사용자가 반복적으로 같은
         실패 메시지를 보던 버그. 이제는 실패한 경우 다음 호출에서 재시도 가능.)
    """
    target = target_date or date.today()

    if not force_regenerate:
        cached = await _get_cached(user_id, target)
        if cached:
            return cached, True

    # Per-(user, date) lock — dedupe LLM cost on concurrent requests.
    lock = _get_or_create_lock((user_id, target))
    async with lock:
        # Double-check: another request may have populated the cache while we
        # were waiting for the lock. Skip the re-check on force_regenerate
        # since the caller explicitly requested a fresh generation.
        if not force_regenerate:
            cached = await _get_cached(user_id, target)
            if cached:
                return cached, True

        # 신규 생성
        yesterday = target - timedelta(days=1)
        greeting = _greeting(target, user_name)
        # main_crop을 동적으로 채우기 위해 placeholder는 일단 일반 표현으로
        prompt = _BRIEFING_PROMPT.format(
            today=target.isoformat(),
            yesterday=yesterday.isoformat(),
            greeting=greeting,
            main_crop="주작물",  # 에이전트가 get_my_farm_profile 호출해 실제값으로 대체
            direction="전일 대비 추세",
        )

        # 직접 LLM 호출 (오케스트레이터 우회).
        # 배경: 오케스트레이터는 브리핑 user 메시지를 farm-data-agent 서브에이전트에 task()
        # 위임하고, 그 서브에이전트의 FARM_DATA_PROMPT 가 옛 5섹션 형식을 강제해 본 모듈의
        # _BRIEFING_PROMPT 가 무력화됐다. 이 경로는 ReAct agent 로 LLM 을 직접 호출 —
        # AGENTS.md 메모리 컨텍스트는 _briefing_system_prompt() 가 명시적으로 합쳐 주입.
        try:
            content = await _generate_via_direct_llm(user_id, prompt)
        except Exception:  # noqa: BLE001 — 직접 호출 실패 시 오케스트레이터로 폴백
            logger.exception("briefing.direct_llm_failed user=%s — orchestrator 폴백", user_id)
            config = {
                "configurable": {
                    "thread_id": f"briefing:v2:{user_id}",
                    "user_id": user_id,
                }
            }
            state = await agent.ainvoke(
                {"messages": [{"role": "user", "content": prompt}]},
                config=config,
            )
            messages = state.get("messages", [])
            content = ""
            if messages:
                content = getattr(messages[-1], "content", "") or ""

        # Strip any leaked meta-reasoning before caching. Gemma occasionally writes
        # a self-monologue ("We have all data. Need to produce…") above the actual
        # markdown despite the prompt forbidding it. The sanitizer trims everything
        # before the first real briefing heading.
        content = _strip_meta_reasoning(content)

        # 실패 응답은 캐시하지 않음 — 다음 호출에서 재시도 가능
        if not content or len(content.strip()) < _MIN_VALID_CONTENT_LEN:
            logger.warning(
                "briefing.empty_or_short user=%s len=%d (캐시 저장 건너뜀)",
                user_id, len(content),
            )
            fallback = (
                f"## 🌅 {target.isoformat()} — 브리핑을 생성하지 못했습니다.\n\n"
                "잠시 후 다시 시도해주세요."
            )
            return fallback, False

        await _save_cached(user_id, target, content)
        return content, False


async def pregenerate_all_users(agent, target_date: date | None = None) -> dict:
    """모든 활성 사용자에 대해 오늘 브리핑을 미리 생성·캐시.

    07:00 KST 스케줄러가 호출. 생성 실패는 사용자별로 격리되어 다른 사용자 진행을 막지
    않는다.

    동시성/타임아웃:
        N=1000 사용자 × 30s/LLM 호출 = 8시간 — 직렬로 돌리면 다음 07:00 전에
        끝나지 않는다. ``FARM_AGENT_PREGEN_CONCURRENCY`` 만큼 병렬, 사용자
        하나당 ``FARM_AGENT_PREGEN_USER_TIMEOUT_SEC`` 로 캡.

    Returns:
        {"total": N, "ok": N_success, "skipped": N_already_cached, "failed": N_error}
    """
    target = target_date or date.today()
    stats = {"total": 0, "ok": 0, "skipped": 0, "failed": 0}
    try:
        from app.models.user import User as _User

        async with async_session() as db:
            rows = await db.execute(select(_User).where(_User.status == 1))
            users = rows.scalars().all()
    except Exception:  # noqa: BLE001 — DB 실패 시 스케줄러는 다음 사이클로 패스
        logger.exception("briefing.pregenerate.user_list_failed")
        return stats

    stats["total"] = len(users)

    concurrency = max(1, getattr(settings, "FARM_AGENT_PREGEN_CONCURRENCY", 4))
    per_user_timeout = max(
        30, getattr(settings, "FARM_AGENT_PREGEN_USER_TIMEOUT_SEC", 180)
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def _process_user(user) -> str:
        # 이미 캐시되어 있으면 스킵 (수동 refresh 가 이미 채운 경우)
        cached = await _get_cached(user.id, target)
        if cached:
            return "skipped"
        async with semaphore:
            try:
                _, was_cached = await asyncio.wait_for(
                    get_or_generate_briefing(
                        agent=agent,
                        user_id=user.id,
                        user_name=user.name or user.id,
                        target_date=target,
                        force_regenerate=True,
                    ),
                    timeout=per_user_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "briefing.pregenerate.user_timeout user=%s timeout=%ss",
                    user.id, per_user_timeout,
                )
                return "failed"
            except Exception:  # noqa: BLE001 — 한 사용자 실패가 전체를 막지 않게
                logger.exception("briefing.pregenerate.user_failed user=%s", user.id)
                return "failed"
        return "skipped" if was_cached else "ok"

    results = await asyncio.gather(
        *[_process_user(u) for u in users], return_exceptions=False
    )
    for outcome in results:
        if outcome in stats:
            stats[outcome] += 1
        else:
            stats["failed"] += 1
    logger.info("briefing.pregenerate.done %s", stats)
    return stats


async def _get_cached(user_id: str, target: date) -> str | None:
    """동일 (user, date) 행이 있으면 content 반환.

    DB 오류는 LLM으로 신선 생성하는 폴백으로 처리하지만, 호출 측에서 빈번한 재생성을
    감지할 수 있도록 explicit warning을 남긴다 (operator 가시성).
    """
    target_dt = datetime.combine(target, datetime.min.time(), tzinfo=timezone.utc)
    try:
        async with async_session() as db:
            result = await db.execute(
                select(FarmAgentBriefing).where(
                    FarmAgentBriefing.user_id == user_id,
                    FarmAgentBriefing.briefing_date == target_dt,
                )
            )
            row = result.scalar_one_or_none()
            return row.content if row else None
    except Exception:  # noqa: BLE001 — DB 실패는 신선 생성으로 폴백
        logger.exception("briefing.cache_read_failed user=%s", user_id)
        return None


async def _save_cached(user_id: str, target: date, content: str) -> None:
    """원자적 UPSERT. 동시 두 요청이 같은 (user, date) 로 들어와도 중복 PK 에러 없이 처리.

    과거 SELECT-then-INSERT 패턴은 동시 요청 시 한 트랜잭션이 성공하고 다른 하나는
    UniqueViolation 으로 실패해 사용자가 두 번 LLM 비용을 부담했다.
    Postgres `INSERT ... ON CONFLICT DO UPDATE` 로 한 번에 처리한다.
    """
    target_dt = datetime.combine(target, datetime.min.time(), tzinfo=timezone.utc)
    try:
        async with async_session() as db:
            stmt = pg_insert(FarmAgentBriefing).values(
                user_id=user_id,
                briefing_date=target_dt,
                content=content,
                generated_at=datetime.now(timezone.utc),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    FarmAgentBriefing.user_id,
                    FarmAgentBriefing.briefing_date,
                ],
                set_={
                    "content": stmt.excluded.content,
                    "generated_at": stmt.excluded.generated_at,
                },
            )
            await db.execute(stmt)
            await db.commit()
    except Exception:  # noqa: BLE001 — 캐시 저장 실패는 응답 자체에는 영향 없음
        logger.exception("briefing.cache_save_failed user=%s", user_id)


def _greeting(d: date, name: str) -> str:
    """요일 기반 한국어 인사."""
    weekday = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"][d.weekday()]
    return f"{name} 사장님, {weekday}입니다"


# Meta-reasoning leak markers. Gemma surfaces these phrases when its "thinking"
# bleeds into the final answer. Detection is conservative — only strips text
# BEFORE the first real briefing heading (## 🌅 / ## or ###), so legitimate
# briefing content is never touched.
_META_REASONING_MARKERS = (
    "We have all data",
    "Need to produce",
    "Let's craft",
    "Let's count",
    "We'll produce",
    "Let me think",
    "I'll produce",
    "I need to",
    "First, ",
    "글자 수",
    "포함할지",
    "We have ",
    "approximate",
    "Let's ",
)


def _strip_meta_reasoning(text: str) -> str:
    """Remove any meta-reasoning preamble before the first markdown heading.

    Strategy:
      1. If the text starts with markdown heading (`##`, `# `, `### `) it's
         already clean — return as-is.
      2. Otherwise, find the first occurrence of `## ` or a line starting with
         a clear briefing emoji marker and slice from there.
      3. As a last resort, if any meta-reasoning marker appears before the
         first heading, drop everything up to that heading.

    Returns the sanitized string, or the original if no sanitization fits.
    """
    if not text:
        return text
    stripped = text.lstrip()
    if stripped.startswith(("## ", "# ", "### ")):
        return stripped

    # Find first heading anywhere in the text.
    heading_idx = -1
    for marker in ("\n## ", "\n# ", "\n### "):
        idx = text.find(marker)
        if idx >= 0 and (heading_idx == -1 or idx < heading_idx):
            heading_idx = idx
    if heading_idx == -1:
        # No heading found — try to detect a briefing-shaped section start.
        for emoji in ("🌅", "🌤️", "⚠️", "🚜", "📋", "💰"):
            idx = text.find(emoji)
            if idx >= 0 and (heading_idx == -1 or idx < heading_idx):
                heading_idx = max(0, text.rfind("\n", 0, idx))

    if heading_idx <= 0:
        return text  # nothing to strip

    preamble = text[:heading_idx]
    if any(marker in preamble for marker in _META_REASONING_MARKERS):
        logger.info("briefing.meta_reasoning_stripped removed_chars=%d", heading_idx)
        return text[heading_idx:].lstrip()
    return text


# ── 테이블 생성 헬퍼 (lifespan 호환성 유지) ──────────────────────────────────


async def purge_cache_if_prompt_changed() -> None:
    """프롬프트 해시가 바뀌면 farm_agent_briefings 의 모든 행을 삭제.

    배경: 프롬프트 구조가 바뀌어도 DB 캐시 행이 남아있으면 사용자는 옛 형식을 계속
    본다. 부팅 시 현재 `_BRIEFING_PROMPT` 의 해시와 meta 테이블에 저장된 직전 해시를
    비교해 다르면 캐시를 비우고 새 해시를 기록한다.
    """
    import hashlib

    from sqlalchemy import text as _sql_text

    from app.core.database import engine

    # Hash BOTH the user-prompt template and the system-prompt base — a tweak
    # to either changes the LLM's behaviour, so cached briefings must be
    # invalidated when either moves.
    combined = (_BRIEFING_PROMPT + "\n---\n" + _BRIEFING_SYSTEM_PROMPT_BASE).encode("utf-8")
    current_hash = hashlib.sha256(combined).hexdigest()[:16]
    try:
        async with engine.begin() as conn:
            await conn.execute(_sql_text(
                "CREATE TABLE IF NOT EXISTS farm_agent_briefing_meta ("
                "  key VARCHAR(40) PRIMARY KEY,"
                "  value VARCHAR(64) NOT NULL"
                ")"
            ))
            row = (await conn.execute(_sql_text(
                "SELECT value FROM farm_agent_briefing_meta WHERE key = 'prompt_hash'"
            ))).first()
            stored = row[0] if row else None
            if stored != current_hash:
                deleted = await conn.execute(_sql_text("DELETE FROM farm_agent_briefings"))
                await conn.execute(_sql_text(
                    "INSERT INTO farm_agent_briefing_meta(key, value) VALUES ('prompt_hash', :h) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
                ), {"h": current_hash})
                logger.info(
                    "briefing.cache.purged_on_prompt_change old=%s new=%s rows=%s",
                    stored, current_hash, getattr(deleted, "rowcount", "?"),
                )
    except Exception:  # noqa: BLE001 — 캐시 정리 실패가 BE 기동 막지 않음
        logger.exception("briefing.cache.purge_failed")


async def ensure_briefing_table(db: AsyncSession | None = None) -> None:
    """farm_agent_briefings 테이블이 없으면 생성.

    이제 `FarmAgentBriefing(Base)` 가 SQLAlchemy ORM 모델로 등록되어 있어
    `Base.metadata.create_all` 이 자동으로 테이블을 만든다. 본 함수는 이전 배포와의
    호환성 유지를 위한 no-op 래퍼로 남겨둔다 (init_db 가 먼저 실행되면 테이블이
    이미 존재하므로 IF NOT EXISTS 가 안전하게 작동한다).
    """
    from sqlalchemy import text

    from app.core.database import engine

    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS farm_agent_briefings ("
            "  user_id VARCHAR(10) NOT NULL,"
            "  briefing_date TIMESTAMPTZ NOT NULL,"
            "  content TEXT NOT NULL,"
            "  generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
            "  PRIMARY KEY (user_id, briefing_date)"
            ")"
        ))
