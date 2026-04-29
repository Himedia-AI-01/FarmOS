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

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import DateTime, String, Text, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, async_session

logger = logging.getLogger(__name__)

# 비어있거나 실패 표식인 응답을 캐시에 저장하지 않기 위한 최소 길이.
# 빈 응답이나 짧은 에러 문자열이 6시간 동안 사용자에게 반복 노출되는 캐시 포이즈닝 방지.
_MIN_VALID_CONTENT_LEN = 60


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
오늘({today})의 농민용 아침 브리핑을 작성해주세요.

## 정보 수집 (다음 5개 도구를 task 위임으로 호출)
1. `get_my_farm_profile` — 사용자 농장 컨텍스트 (작물·지역·면적)
2. `get_current_weather` — 오늘 날씨 + 단기 예보
3. `get_market_prices` — 내 주작물 카테고리(채소류/과일류) 시세
4. `get_recent_iot_decisions(hours=24)` — 어제 자율 IoT 제어 이력
5. `get_journal_daily_summary("{yesterday}")` — 어제 영농일지 요약

## 브리핑 작성 — 다음 5섹션 형식 엄수
사장님이 스마트폰으로 1화면(600자 이하)에 읽을 수 있어야 합니다.
존댓말 사용. "사장님" 호칭. 추측 금지 (데이터 없으면 "확인 어려움" 표시).

```
## 🌅 {today} {greeting}

### ⚠️ 오늘의 알림 (해당 시에만)
[강수확률 ≥30% / 풍속 ≥3m/s / 기온 ≥30℃ / 일지 누락 / IoT 비정상 — 1-2줄. 해당 없으면 섹션 생략]

### 🌤️ 오늘 날씨
[기온 ○○~○○℃, 강수 ○○%, 풍속 ○○m/s. 한 줄 작업 조언.]
(출처: 기상청)

### 🚜 오늘 권장 작업 (1-3개)
- [날씨·작물 단계 종합한 구체 작업. 농약 제품명·자격 판정은 절대 언급 금지.]
- [예: "오전 7-10시 사이 잎 살피기 (이슬 마른 후, 풍속 약함)"]

### 📋 어제 농장 상태
- IoT 제어: ○○회 (관수 ○회, 환기 ○회)
- 일지: [요약 1줄. 누락 있으면 "보충 필요" 명시]

### 💰 {main_crop} 시세
- 현재 ○○원/kg ({direction}) (출처: KAMIS)
- [한 줄 추세 코멘트]
```

## 절대 금지
- 농약 제품명·희석배수·살포 시기 추천 (별도 챗봇 안내로 유도)
- 직불금·자격 판정 (별도 챗봇 안내로 유도)
- "곧", "조만간" 같은 모호 표현 (구체 시간 또는 "정보 없음")
- 도구가 반환하지 않은 정보 추가
"""


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

    config = {
        "configurable": {
            "thread_id": f"briefing:{user_id}",  # stateful — 어제 브리핑 참고
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


# ── 테이블 생성 헬퍼 (lifespan 호환성 유지) ──────────────────────────────────


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
