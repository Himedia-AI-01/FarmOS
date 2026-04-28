"""공익직불사업 (정부 지원금) API 엔드포인트.

Phase 1 — 결정적 REST 엔드포인트:
    GET  /subsidy/match             사용자 자격 매칭 (카드 목록용)
    POST /subsidy/ask               자연어 질의응답 (RAG + LLM)
    GET  /subsidy/detail/{code}     지원금 상세 정보 (드로어 UI)

Phase 2 (예정):
    POST /subsidy/chat              deep agent 기반 대화형 엔드포인트
    — 기존 /match, /ask 는 그대로 유지 (deterministic UI flow 용)
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.subsidy import (
    MatchResponse,
    SubsidyAskRequest,
    SubsidyAskResponse,
    SubsidyDetail,
)
from app.services.subsidy.prompts import SUBSIDY_SYSTEM_PROMPT, build_answer_prompt
from app.services.subsidy.tools import (
    get_subsidy_details,
    get_user_profile,
    list_eligible_subsidies,
    search_subsidy_regulations,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subsidy", tags=["subsidy"])


# ── 매칭 (카드 목록) ───────────────────────────────────────


@router.get("/match", response_model=MatchResponse)
async def match_subsidies(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MatchResponse:
    """현재 사용자 프로필로 모든 지원금의 자격을 판정한다.

    반환: eligible / ineligible / needs_review 3 분류
    """
    profile = await get_user_profile(db, user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="사용자 프로필을 찾을 수 없습니다.")
    return await list_eligible_subsidies(db, profile)


# ── 자연어 질의응답 (RAG + LLM) ──────────────────────────


@router.post("/ask", response_model=SubsidyAskResponse)
async def ask_subsidy(
    req: SubsidyAskRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SubsidyAskResponse:
    """시행지침에 대한 자연어 질문에 답변한다 (RAG + LLM)."""
    # 1. 시행지침 검색 (동기 Solar 임베딩·리랭커 호출 → asyncio.to_thread로 이벤트루프 보호)
    #    top_k=3: LLM 입력 토큰·처리시간 절감 (UI 에도 3건만 표시)
    citations = await asyncio.to_thread(search_subsidy_regulations, req.question, 3)
    if not citations:
        return SubsidyAskResponse(
            question=req.question,
            answer=(
                "죄송합니다. 이 질문에 대한 2026년도 기본형 공익직불사업 시행지침 조항을 "
                "찾지 못했습니다. 농관원(1334) 또는 지자체 담당자에게 문의해주세요."
            ),
            citations=[],
            escalation_needed=True,
        )

    # 2. 사용자 프로필 요약 (답변 개인화용)
    profile = await get_user_profile(db, user.id)
    profile_summary = _format_profile_summary(profile) if profile else None

    # 3. 인용 조항 텍스트 구성
    citations_text = "\n\n".join(
        f"[인용 {i + 1}] {c.chapter} > {c.article}\n{c.snippet}"
        for i, c in enumerate(citations)
    )

    # 4. LLM 호출 (OpenRouter 직결, google/gemma-4-31b-it)
    user_prompt = build_answer_prompt(
        question=req.question,
        citations_text=citations_text,
        profile_summary=profile_summary,
    )
    try:
        answer = await _call_llm(SUBSIDY_SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        logger.exception(f"LLM 호출 실패: {e}")
        raise HTTPException(
            status_code=503,
            detail="답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
        ) from e

    return SubsidyAskResponse(
        question=req.question,
        answer=answer,
        citations=citations,
        escalation_needed=False,
    )


# ── 지원금 상세 (드로어 UI) ──────────────────────────────


@router.get("/detail/{subsidy_code}", response_model=SubsidyDetail)
async def get_detail(
    subsidy_code: str,
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SubsidyDetail:
    """지원금 코드로 상세 정보 조회."""
    detail = await get_subsidy_details(db, subsidy_code)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"지원금 '{subsidy_code}'를 찾을 수 없습니다.")
    return detail


# ── 내부 헬퍼 ──────────────────────────────────────────────


def _format_profile_summary(profile) -> str:
    """사용자 프로필을 LLM에 전달할 자연어 요약으로 변환."""
    parts: list[str] = [f"경작 면적 {profile.area_ha}ha"]
    if profile.farmland_type:
        parts.append(f"농지 유형 {profile.farmland_type}")
    parts.append("진흥지역" if profile.is_promotion_area else "비진흥지역")
    parts.append("농업경영체 등록 완료" if profile.has_farm_registration else "경영체 미등록")
    parts.append(f"영농 경력 {profile.years_farming}년")
    parts.append(f"농촌 거주 {profile.years_rural_residence}년")
    if profile.farmer_type and profile.farmer_type != "일반":
        parts.append(f"{profile.farmer_type} 농업인")
    return ", ".join(parts)


async def _call_llm(system_prompt: str, user_prompt: str) -> str:
    """공익직불 전용 LLM 호출. LiteLLM 프록시 경유 (팀 API 사용량 통합 추적).

    diagnosis.py 와 동일한 컨벤션:
      - settings.LITELLM_API_KEY / settings.LITELLM_URL 사용
      - async with httpx.AsyncClient 로 커넥션 누수 방지
      - extra_body.reasoning.exclude=True 로 reasoning 토큰 출력 제외

    속도 튜닝:
      - max_tokens=500: 장문 답변의 꼬리 지연 제거 (시행지침 Q&A 는 3~5 문장이면 충분)
      - temperature=0.2: 인용 기반 답변이므로 낮은 온도가 일관성·속도에 유리
    """
    api_key = settings.LITELLM_API_KEY
    if not api_key:
        raise RuntimeError(
            "LITELLM_API_KEY 가 설정되지 않았습니다. .env 파일을 확인하세요."
        )

    async with httpx.AsyncClient(
        http1=True,
        http2=False,
        timeout=httpx.Timeout(60.0, connect=20.0),
    ) as http_client:
        llm = ChatOpenAI(
            model=settings.SUBSIDY_LLM_MODEL,
            base_url=settings.LITELLM_URL,
            api_key=api_key,
            temperature=0.2,
            max_tokens=500,
            http_async_client=http_client,
            extra_body={"reasoning": {"effort": "minimal", "exclude": True}},
        )
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])
    content = response.content
    return content if isinstance(content, str) else str(content)
