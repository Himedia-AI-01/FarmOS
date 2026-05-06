"""공익직불 도메인 tool 함수 — Phase 1 REST + Phase 2 deep agent 공통.

설계 원칙 (주요 결정):
    모든 도메인 능력을 독립적·구성가능한 함수로 구현.
    현재는 REST 엔드포인트에서 직접 호출되지만, 추후 deep agent 로 전환 시
    동일 함수에 `@tool` 데코레이터만 부여하면 agent 도구로 재사용 가능.

입력/출력:
    - 모든 함수는 Pydantic 타입(schemas.subsidy) 을 입출력으로 사용
    - DB 세션과 user_id는 명시적 파라미터 (agent 전환 시 RunnableConfig 로 주입)
    - 부작용 없는 read-only (현재 모든 tool 은 조회성).

도구 목록:
    get_user_profile(db, user_id) → UserProfile
    list_eligible_subsidies(db, profile) → MatchResponse
    check_eligibility_rule(db, profile, subsidy_code) → EligibilityResult
    search_subsidy_regulations(query, top_k) → list[Citation]
    get_subsidy_details(db, subsidy_code) → SubsidyDetail
"""

from __future__ import annotations

import logging
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subsidy import Subsidy
from app.models.user import User
from app.schemas.subsidy import (
    Citation,
    EligibilityResult,
    MatchResponse,
    SubsidyDetail,
    UserProfile,
)
from app.services.subsidy.gov_rag import GovSubsidyRAG
from app.services.subsidy.matcher import dispatch_eligibility, match_user

logger = logging.getLogger(__name__)

# User 모델의 area 필드는 평(pyeong) 단위로 저장됨
# (frontend/src/constants/farming.ts의 safeAreaConvert 참조).
# 시행지침 자격 판정은 모두 ha 단위이므로 반드시 변환 필요.
# 1평 = 3.306 m², 10,000 m² = 1 ha → 1평 = 0.0003306 ha
PYEONG_TO_HA = 3.306 / 10_000


# ── 1. 사용자 프로필 ────────────────────────────────────────


async def get_user_profile(db: AsyncSession, user_id: str) -> UserProfile | None:
    """User 모델에서 공익직불 매칭용 프로필을 추출한다.

    단위 변환 주의:
        User.area 는 평(pyeong) 단위로 저장되어 있음.
        시행지침 자격 판정은 ha 단위이므로 반드시 변환.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return None
    area_pyeong = user.area or 0.0
    area_ha = round(area_pyeong * PYEONG_TO_HA, 4)
    return UserProfile(
        user_id=user.id,
        location=user.location or "",
        area_ha=area_ha,
        main_crop=user.main_crop or "",
        farmland_type=user.farmland_type or "",
        is_promotion_area=user.is_promotion_area,
        has_farm_registration=user.has_farm_registration,
        farmer_type=user.farmer_type or "일반",
        years_rural_residence=user.years_rural_residence or 0,
        years_farming=user.years_farming or 0,
    )


# ── 2. 전체 자격 매칭 ─────────────────────────────────────


async def list_eligible_subsidies(db: AsyncSession, profile: UserProfile) -> MatchResponse:
    """사용자 프로필에 대해 등록된 모든 지원금의 자격을 판정한다."""
    result = await db.execute(select(Subsidy).where(Subsidy.is_active.is_(True)))
    subsidies = list(result.scalars().all())
    return match_user(profile, subsidies)


# ── 3. 특정 프로그램 자격 체크 ───────────────────────────


async def check_eligibility_rule(
    db: AsyncSession, profile: UserProfile, subsidy_code: str
) -> EligibilityResult | None:
    """특정 지원금 코드에 대한 자격 판정만 수행."""
    result = await db.execute(
        select(Subsidy).where(Subsidy.code == subsidy_code, Subsidy.is_active.is_(True))
    )
    subsidy = result.scalar_one_or_none()
    if subsidy is None:
        return None
    return dispatch_eligibility(profile, subsidy)


# ── 4. 시행지침 RAG 검색 ─────────────────────────────────


@lru_cache(maxsize=1)
def _get_rag() -> GovSubsidyRAG:
    """프로세스당 한 번만 초기화되는 RAG 싱글톤.

    GovSubsidyRAG 생성자는 Upstage 클라이언트·ChromaDB 핸들을 연다.
    매 요청마다 재생성하면 오버헤드 크므로 캐시.
    """
    return GovSubsidyRAG()


@lru_cache(maxsize=256)
def _search_subsidy_regulations_cached(query: str, top_k: int) -> tuple[Citation, ...]:
    """LRU 캐시 적중 시 즉시 반환 — 성공 케이스만 캐시한다.

    실패는 캐시에 저장하면 안 되므로 본 함수 안에서 예외 발생 시 캐시가 만들어지지
    않도록 호출자(`search_subsidy_regulations`)가 try/except로 감싼다.
    """
    rag = _get_rag()
    return tuple(rag.search(query, top_k=top_k))


@lru_cache(maxsize=256)
def _search_subsidy_regulations_fast_cached(query: str, top_k: int) -> tuple[Citation, ...]:
    """Reranker 없는 경량 검색 — Solar embedding + ChromaDB top_k 만.

    리랭커 단계 (cross-encoder, ~수 초) 를 건너뛴다. ~500ms 수준의 빠른 검색.
    """
    rag = _get_rag()
    return tuple(rag.search_fast(query, top_k=top_k))


def search_subsidy_regulations(query: str, top_k: int = 5) -> list[Citation]:
    """자연어 질의에 가장 관련 높은 시행지침 조항을 반환한다.

    Solar asymmetric embedding + bge-reranker-v2-m3-ko + 타이틀 키워드 부스트.

    캐시 정책:
      - 성공 결과는 `_search_subsidy_regulations_cached`(LRU 256)에 보관.
      - 일시적 실패(Solar 타임아웃, ChromaDB 락 등)는 절대 캐시되지 않음.
        과거 단일 데코레이터 구현은 빈 리스트가 캐시되어 프로세스 재시작 전까지
        영구적으로 "검색 결과 없음"을 반환하는 영구 폴백 버그가 있었다.
    """
    try:
        return list(_search_subsidy_regulations_cached(query, top_k))
    except RuntimeError as e:
        logger.error(f"RAG 초기화 실패: {e}")
        return []
    except Exception as e:  # noqa: BLE001 — 실패는 캐시 미스로 다음 호출에서 재시도 가능해야 함
        logger.warning("search_subsidy_regulations.failed query=%s err=%s", query, e)
        return []


# Auto-escalation threshold: if fast search's top similarity falls below this,
# automatically re-run with the full reranker pipeline. This is deterministic
# (Python-side) rather than prompt-dependent — research (2026 production patterns)
# shows LLM agents frequently ignore "if low confidence, retry" prompt rules,
# especially under short-context / no-reasoning configurations.
_FAST_SEARCH_CONFIDENCE_THRESHOLD = 0.5


def search_subsidy_regulations_fast(query: str, top_k: int = 5) -> list[Citation]:
    """빠른 시행지침 검색 + 자동 escalation.

    1) 리랭커 스킵 fast 검색 시도 (~500ms).
    2) 최고 similarity 가 0.5 미만이면 자동으로 full 정밀 검색으로 escalate.
       ("critique-refine" 패턴 — 호출자가 신뢰도 판단을 안 해도 안전.)

    호출자(에이전트)는 단일 도구만 알면 되므로 prompt 가 짧아지고 결정성이
    좋아진다. 호출 결과의 `similarity` 필드로 어느 경로가 사용됐는지 식별 가능.
    """
    try:
        fast = list(_search_subsidy_regulations_fast_cached(query, top_k))
    except RuntimeError as e:
        logger.error(f"RAG 초기화 실패: {e}")
        return []
    except Exception as e:  # noqa: BLE001
        logger.warning("search_subsidy_regulations_fast.failed query=%s err=%s", query, e)
        return []

    if not fast:
        # 0건 — full 검색이 더 잘 잡을 수도 있음
        logger.info("fast_search.empty query=%r → escalating to precise", query)
        return search_subsidy_regulations(query, top_k=top_k)

    top_sim = max((c.similarity or 0.0) for c in fast)
    if top_sim < _FAST_SEARCH_CONFIDENCE_THRESHOLD:
        logger.info(
            "fast_search.low_confidence query=%r top_sim=%.3f → escalating to precise",
            query, top_sim,
        )
        precise = search_subsidy_regulations(query, top_k=top_k)
        # precise 결과가 더 좋으면 그것을, 아니면 fast 유지
        if precise:
            precise_top = max((c.similarity or 0.0) for c in precise)
            if precise_top > top_sim:
                return precise
    return fast


# ── 5. 지원금 상세 정보 ───────────────────────────────────


async def get_subsidy_details(db: AsyncSession, subsidy_code: str) -> SubsidyDetail | None:
    """코드로 지원금 상세 정보를 조회. 카드/드로어 UI 용."""
    result = await db.execute(
        select(Subsidy).where(Subsidy.code == subsidy_code, Subsidy.is_active.is_(True))
    )
    subsidy = result.scalar_one_or_none()
    if subsidy is None:
        return None
    return SubsidyDetail.model_validate(subsidy, from_attributes=True)
