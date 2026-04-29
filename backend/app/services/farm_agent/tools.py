"""FarmOS Deep Agent 도구 정의 — 기존 서비스의 얇은 래퍼.

설계 원칙:
  - 비즈니스 로직 추가 금지. 모든 도구는 기존 service/core 함수의 얇은 래퍼.
  - DB·user_id 같은 런타임 의존성은 RunnableConfig.configurable 로 주입.
    (서브에이전트가 LLM 인자로 user_id를 다루지 않게 해 PII 누출 방지)
  - 도구는 항상 짧은 문자열·JSON을 반환. 거대한 dict 반환은 컨텍스트 오염.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from sqlalchemy import select

from app.core.database import async_session
from app.core.journal_store import get_daily_summary, list_entries
from app.core.weather_client import get_weather
from app.models.ai_agent import AiAgentDecision
from app.models.user import User
from app.services.diagnosis_agent import run_diagnosis
from app.services.kamis import kamis_service
from app.services.subsidy.tools import (
    check_eligibility_rule as _check_eligibility_rule,
    get_subsidy_details as _get_subsidy_details,
    get_user_profile as _get_user_profile,
    list_eligible_subsidies as _list_eligible_subsidies,
    search_subsidy_regulations as _search_subsidy_regulations,
)

logger = logging.getLogger(__name__)


def _user_id(config: RunnableConfig) -> str | None:
    """RunnableConfig에서 user_id 추출. 비로그인 호출은 None."""
    return (config or {}).get("configurable", {}).get("user_id")


# ── 0. 사용자 프로필 (개인화 메모리) ───────────────────────────────────────


@tool
async def get_my_farm_profile(config: RunnableConfig) -> str:
    """현재 로그인 사용자의 농장 프로필을 반환한다 (개인화 답변용).

    포함 정보: 이름, 농장명, 지역, 면적(평), 주작물, 농지유형, 농민유형, 영농 경력 등.
    사용자가 "내 농장에 맞는 추천" 같은 요청을 하면 본 도구를 먼저 호출해 컨텍스트를 확보.
    """
    user_id = _user_id(config)
    if not user_id:
        return "비로그인 상태입니다. 개인화된 정보를 제공할 수 없습니다."
    from sqlalchemy import select as _select

    async with async_session() as db:
        result = await db.execute(_select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    if user is None:
        return "사용자 프로필을 찾을 수 없습니다."

    profile = {
        "name": user.name,
        "farmname": user.farmname or "",
        "location": user.location or "",
        "area_pyeong": user.area or 0,
        "main_crop": user.main_crop or "",
        "crop_variety": user.crop_variety or "",
        "farmland_type": user.farmland_type or "",
        "farmer_type": user.farmer_type or "일반",
        "years_farming": user.years_farming or 0,
        "years_rural_residence": user.years_rural_residence or 0,
        "is_promotion_area": bool(user.is_promotion_area),
        "has_farm_registration": bool(user.has_farm_registration),
    }
    return json.dumps(profile, ensure_ascii=False)


# ── 1. 진단 ────────────────────────────────────────────────────────────────


@tool
async def diagnose_pest(pest: str, crop: str, region: str) -> str:
    """FarmOS 병해충 진단 파이프라인 실행 (날씨 + NCPMS + 농약 DB).

    Args:
        pest: 해충/병명 (예: "노균병", "진딧물")
        crop: 작물 (예: "토마토", "사과")
        region: 농장 지역 (예: "경북 영주시")

    Returns:
        진단 결과 마크다운 (권장 농약, NCPMS 방제법, 날씨 조언 포함).

    Safety:
        파이프라인 실패 시 절대 빈/가짜 응답을 돌려주지 않는다.
        대신 어느 단계에서 실패했는지 명시한 에러 메시지를 반환해
        verifier-agent / orchestrator가 농약 추천을 임의 합성하지 못하게 한다.
    """
    final = ""
    completed_nodes: list[str] = []
    try:
        async for node, payload in run_diagnosis(pest=pest, crop=crop, region=region):
            completed_nodes.append(node)
            if node == "generate_diagnosis":
                result = payload.get("analysis_result") or {}
                final = result.get("result_text", "")
    except Exception as exc:  # noqa: BLE001 — 안전 민감 도메인에서 모든 예외를 사용자 가시화
        last_node = completed_nodes[-1] if completed_nodes else "(시작 전)"
        logger.exception(
            "diagnose_pest.failed pest=%s crop=%s region=%s last_node=%s",
            pest, crop, region, last_node,
        )
        return (
            "[진단_도구_오류] 진단 파이프라인이 실패했습니다 "
            f"(마지막 성공 단계: {last_node}, 사유: {type(exc).__name__}). "
            "농약·방제 정보를 임의로 생성하지 마세요. "
            "사용자에게 '일시적 오류로 진단을 완료하지 못했다'고 안내하고 잠시 후 재시도를 권하세요."
        )

    if not final:
        # generate_diagnosis 노드가 끝까지 도달하지 못했거나 result_text가 비어있음.
        logger.warning(
            "diagnose_pest.no_result pest=%s crop=%s region=%s nodes=%s",
            pest, crop, region, completed_nodes,
        )
        return (
            "[진단_결과_없음] 도구가 결과 텍스트를 생성하지 못했습니다 "
            f"(통과 노드: {completed_nodes or '없음'}). "
            "농약·방제 정보를 임의로 생성하지 마세요. 사용자에게 데이터 부족을 안내하세요."
        )
    return final


# ── 2. 공익직불 ─────────────────────────────────────────────────────────────


@tool
async def list_eligible_subsidies(config: RunnableConfig) -> str:
    """현재 로그인 사용자의 모든 등록 지원금 자격 매칭."""
    user_id = _user_id(config)
    if not user_id:
        return "비로그인 상태에서는 자격 매칭을 제공할 수 없습니다."
    async with async_session() as db:
        profile = await _get_user_profile(db, user_id)
        if profile is None:
            return "사용자 프로필을 찾을 수 없습니다."
        result = await _list_eligible_subsidies(db, profile)
    return result.model_dump_json(exclude_none=True)


@tool
async def check_eligibility_rule(subsidy_code: str, config: RunnableConfig) -> str:
    """특정 지원금 코드에 대한 자격 판정만 수행."""
    user_id = _user_id(config)
    if not user_id:
        return "비로그인 상태에서는 자격 판정을 제공할 수 없습니다."
    async with async_session() as db:
        profile = await _get_user_profile(db, user_id)
        if profile is None:
            return "사용자 프로필을 찾을 수 없습니다."
        result = await _check_eligibility_rule(db, profile, subsidy_code)
    if result is None:
        return f"코드 {subsidy_code}에 해당하는 지원금이 없습니다."
    return result.model_dump_json(exclude_none=True)


@tool
async def search_subsidy_regulations(query: str, top_k: int = 5) -> str:
    """공익직불 시행지침에서 가장 관련 있는 조항을 검색.

    반드시 응답 시 [doc > 조] 형식의 인용을 유지하세요.

    구현 노트: 내부 RAG 함수(_search_subsidy_regulations)는 동기 함수로 Solar HTTP +
    ChromaDB + bge-reranker 호출이 수백 ms~수 초 걸린다. asyncio.to_thread로 워커 스레드에
    오프로드해 FastAPI 이벤트 루프 블록을 방지한다.
    """
    import asyncio
    citations = await asyncio.to_thread(_search_subsidy_regulations, query, top_k)
    if not citations:
        return "검색된 조항이 없습니다."
    return json.dumps(
        [c.model_dump(exclude_none=True) for c in citations],
        ensure_ascii=False,
    )


@tool
async def get_subsidy_details(subsidy_code: str) -> str:
    """지원금 코드로 상세 정보 조회 (카드/드로어 UI 데이터)."""
    async with async_session() as db:
        detail = await _get_subsidy_details(db, subsidy_code)
    if detail is None:
        return f"코드 {subsidy_code} 지원금을 찾을 수 없습니다."
    return detail.model_dump_json(exclude_none=True)


# ── 3. 농장 데이터 (읽기 전용) ──────────────────────────────────────────────


@tool
async def get_current_weather() -> str:
    """KMA 초단기실황 + 예보. 사용자 농장 격자좌표 기반 (config FARM_NX/NY)."""
    data = await get_weather()
    return json.dumps(data, ensure_ascii=False, default=str)


@tool
async def get_market_prices(category_name: str = "") -> str:
    """KAMIS 일별 부류별 도·소매가격 최신 스냅샷.

    Args:
        category_name: 부류 한글명 (예: "채소류", "과일류"). 빈 값이면 전체.

    Returns:
        품목별 최신 가격 JSON 배열. 항목당 item_name, unit, dpr1(당일 가격),
        direction(전일 대비) 등.
    """
    items = await kamis_service.get_latest_prices()
    if category_name:
        items = [it for it in items if category_name in (it.get("category_name") or "")]
    return json.dumps(items[:30], ensure_ascii=False, default=str)


@tool
async def list_journal_entries(
    days: int = 7,
    crop: str = "",
    config: RunnableConfig = None,
) -> str:
    """최근 N일치 영농일지 (로그인 사용자).

    Args:
        days: 며칠 전부터 (기본 7).
        crop: 작물 필터 (빈 값이면 전체).
    """
    user_id = _user_id(config)
    if not user_id:
        return "비로그인 상태에서는 영농일지를 조회할 수 없습니다."
    today = date.today()
    date_from = today - timedelta(days=max(1, days) - 1)
    async with async_session() as db:
        entries, total = await list_entries(
            db,
            user_id=user_id,
            page=1,
            page_size=50,
            date_from=date_from,
            date_to=today,
            crop=crop or None,
        )
    payload = [
        {
            "id": e.id,
            "work_date": e.work_date.isoformat() if e.work_date else None,
            "crop": e.crop,
            "field_name": e.field_name,
            "work_stage": e.work_stage,
            "weather": e.weather,
            "detail": e.detail,
        }
        for e in entries
    ]
    return json.dumps({"total": total, "items": payload}, ensure_ascii=False)


@tool
async def get_journal_daily_summary(target_date: str, config: RunnableConfig) -> str:
    """특정 날짜의 영농일지 요약 (LLM 생성, 누락 항목 체크 포함).

    Args:
        target_date: 'YYYY-MM-DD' 형식.
    """
    user_id = _user_id(config)
    if not user_id:
        return "비로그인 상태에서는 일일 요약을 제공할 수 없습니다."
    try:
        d = date.fromisoformat(target_date)
    except ValueError:
        return f"날짜 형식 오류: '{target_date}'. YYYY-MM-DD 형식을 사용하세요."
    async with async_session() as db:
        summary = await get_daily_summary(db, user_id, d)
    return json.dumps(summary, ensure_ascii=False, default=str)


@tool
async def get_recent_iot_decisions(hours: int = 24, control_type: str = "") -> str:
    """자율 IoT 에이전트의 최근 제어 판단 이력.

    Args:
        hours: 최근 N시간 (기본 24).
        control_type: ventilation | irrigation | lighting | shading | "" (전체).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, hours))
    async with async_session() as db:
        stmt = select(AiAgentDecision).where(AiAgentDecision.timestamp >= cutoff)
        if control_type:
            stmt = stmt.where(AiAgentDecision.control_type == control_type)
        stmt = stmt.order_by(AiAgentDecision.timestamp.desc()).limit(30)
        rows = (await db.execute(stmt)).scalars().all()
    payload = [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "control_type": r.control_type,
            "priority": r.priority,
            "source": r.source,
            "reason": r.reason,
            "action": r.action,
            "duration_ms": r.duration_ms,
        }
        for r in rows
    ]
    return json.dumps({"count": len(payload), "items": payload}, ensure_ascii=False)


# ── 도구 그룹 (서브에이전트별 주입용) ───────────────────────────────────────


DIAGNOSIS_TOOLS = [diagnose_pest, get_my_farm_profile]

SUBSIDY_TOOLS = [
    get_my_farm_profile,
    list_eligible_subsidies,
    check_eligibility_rule,
    search_subsidy_regulations,
    get_subsidy_details,
]

FARM_DATA_TOOLS = [
    get_my_farm_profile,
    get_current_weather,
    get_market_prices,
    list_journal_entries,
    get_journal_daily_summary,
    get_recent_iot_decisions,
]

# Tools exposed to the top-level orchestrator.
#
# Deep Agents gives the orchestrator a `task` tool for subagent delegation, but
# Gemma often chooses direct, concrete tool names for simple data lookups
# ("get_current_weather", "get_market_prices", ...). If these first-party tools
# are only attached to subagents, the top-level model can truthfully say it
# cannot call them. Binding the safe/read-only tools directly keeps simple
# requests cheap and prevents that apology path, while diagnosis/subsidy still
# remain available for more complex delegated flows.
ORCHESTRATOR_TOOLS = [
    get_my_farm_profile,
    get_current_weather,
    get_market_prices,
    list_journal_entries,
    get_journal_daily_summary,
    get_recent_iot_decisions,
    list_eligible_subsidies,
    check_eligibility_rule,
    search_subsidy_regulations,
    get_subsidy_details,
]
