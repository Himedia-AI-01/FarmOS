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
import json
import logging
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from sse_starlette.sse import EventSourceResponse
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.subsidy import (
    ChatTurn,
    ClauseLookup,
    ClauseSnippetsRequest,
    ClauseSnippetsResponse,
    MatchResponse,
    SubsidyAskRequest,
    SubsidyDetail,
)
from app.services.subsidy.matcher import fetch_clause_snippet
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


# ── 조항 발췌 (lazy lookup) ────────────────────────────────


@router.post("/clauses/snippets", response_model=ClauseSnippetsResponse)
async def get_clause_snippets(
    req: ClauseSnippetsRequest,
    _: User = Depends(get_current_user),
) -> ClauseSnippetsResponse:
    """클라이언트가 detail drawer 를 열 때 호출. 각 clause 의 시행지침 원문 발췌 반환.

    /match 응답에 포함하지 않는 이유: RAG 콜드 스타트가 ~21s/clause 라
    /match 인라인하면 첫 사용자 경험을 망친다. 여기로 분리하면:
      - /match 는 즉시 (rule-based 만)
      - 사용자가 카드를 열면 백그라운드에서 fetch (drawer 가 즉시 열림 + 점진적 enrich)
      - 같은 (tag, text) 는 process-level lru_cache 로 instant on warm

    조회 실패 항목은 응답 dict 에서 제외 → 클라이언트는 키 부재로 판단.
    """
    # fetch_clause_snippet 는 Solar 임베딩(HTTP) + ChromaDB(I/O) 위주라 동시 실행 시
    # 잘 겹친다. asyncio.gather 로 병렬화하면 6개 clause 가 단일 호출 시간 안에 완료.
    # to_thread 로 감싸 동기 함수를 별도 워커에 위임 (이벤트루프 차단 방지).
    async def _one(item: ClauseLookup) -> tuple[str, str | None]:
        try:
            snip = await asyncio.to_thread(fetch_clause_snippet, item.tag, item.text)
            return item.tag, snip
        except Exception as e:
            logger.warning(f"clause snippet 조회 실패 ({item.tag}): {e}")
            return item.tag, None

    results = await asyncio.gather(*(_one(it) for it in req.items))
    snippets: dict[str, str] = {tag: snip for tag, snip in results if snip}
    return ClauseSnippetsResponse(snippets=snippets)


# ── 자연어 질의응답 (RAG + LLM) ──────────────────────────


@router.post("/ask")
async def ask_subsidy(
    req: SubsidyAskRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    """시행지침에 대한 자연어 질문에 SSE 로 답변 스트리밍 (RAG + LLM).

    SSE 이벤트 형식 (각 frame 의 `data` 는 JSON 문자열):
      {"type": "delta",  "content": "..."}            # 답변 텍스트 청크 (여러 번)
      {"type": "done",   "citations": [...],           # 마지막 1회
                         "escalation_needed": bool,
                         "faithfulness_warnings": [...]}
      {"type": "error",  "message": "..."}            # 실패 시 1회
    """
    # ─── 1. 사전작업 (스트리밍 외부에서) ────────────────────
    # 병렬 사전작업: rewrite + profile.
    rewrite_task = (
        asyncio.create_task(_rewrite_query_for_rag(req.question))
        if _needs_rewrite(req.question)
        else None
    )
    profile_task = asyncio.create_task(get_user_profile(db, user.id))
    rag_query = await rewrite_task if rewrite_task else req.question
    profile = await profile_task

    # 시행지침 검색 (top_k=5)
    citations = await asyncio.to_thread(search_subsidy_regulations, rag_query, 5)

    if citations:
        retrieved = " | ".join(
            f"{c.article} (sim={c.similarity:.3f})" for c in citations
        )
        logger.info(f"[/ask] q={req.question!r} rewritten={rag_query!r} → {retrieved}")
    else:
        logger.warning(f"[/ask] q={req.question!r} rewritten={rag_query!r} → no hits")

    profile_summary = _format_profile_summary(profile) if profile else None

    # 인용 조항 텍스트 구성
    citations_text = "\n\n".join(
        f"[인용 {i + 1}] {c.chapter} > {c.article}\n{c.snippet}"
        for i, c in enumerate(citations)
    )

    user_prompt = build_answer_prompt(
        question=req.question,
        citations_text=citations_text,
        profile_summary=profile_summary,
    )
    messages = _build_chat_messages(
        system_prompt=SUBSIDY_SYSTEM_PROMPT,
        history=req.history,
        latest_user_prompt=user_prompt,
    )
    citations_payload = [c.model_dump() for c in citations]

    # ─── 2. SSE 제너레이터 ────────────────────────────────
    async def event_stream():
        # citation 이 없는 경우 — escalation 메시지를 한 번에 보내고 종료.
        if not citations:
            escalation_msg = (
                "죄송합니다. 이 질문에 대한 2026년도 기본형 공익직불사업 시행지침 조항을 "
                "찾지 못했습니다. 농관원(1334) 또는 지자체 담당자에게 문의해주세요."
            )
            yield {"data": json.dumps({"type": "delta", "content": escalation_msg}, ensure_ascii=False)}
            yield {"data": json.dumps({
                "type": "done",
                "citations": [],
                "escalation_needed": True,
                "faithfulness_warnings": [],
            }, ensure_ascii=False)}
            return

        # 정상 경로 — LLM 청크를 그대로 전달.
        full_answer_parts: list[str] = []
        try:
            async for chunk in _stream_llm(messages):
                full_answer_parts.append(chunk)
                yield {"data": json.dumps({"type": "delta", "content": chunk}, ensure_ascii=False)}
        except RuntimeError as e:
            # 설정 오류 (예: LITELLM_API_KEY 누락) — 사용자가 알아도 행동 못 함, 일반화된 메시지.
            logger.error(f"LLM 설정 오류: {e}")
            yield {"data": json.dumps({"type": "error", "message": "서버 설정 오류"}, ensure_ascii=False)}
            return
        except (
            AuthenticationError, PermissionDeniedError, BadRequestError, NotFoundError,
        ) as e:
            logger.error(f"LLM 설정/요청 오류 ({type(e).__name__}): {e}")
            yield {"data": json.dumps({"type": "error", "message": "LLM 공급자가 요청을 거절했습니다."}, ensure_ascii=False)}
            return
        except (
            APITimeoutError, APIConnectionError, RateLimitError, InternalServerError,
        ) as e:
            logger.warning(f"LLM 일시 장애 ({type(e).__name__}): {e}")
            yield {"data": json.dumps({"type": "error", "message": "답변 생성 중 일시적 오류가 발생했습니다. 잠시 후 다시 시도해주세요."}, ensure_ascii=False)}
            return
        except APIStatusError as e:
            logger.error(f"LLM HTTP {e.status_code}: {e}")
            yield {"data": json.dumps({"type": "error", "message": "답변 생성 중 상류 오류가 발생했습니다."}, ensure_ascii=False)}
            return
        except Exception as e:
            logger.exception(f"LLM 호출 중 예상 못한 오류: {e}")
            yield {"data": json.dumps({"type": "error", "message": "내부 오류"}, ensure_ascii=False)}
            return

        # 빈 응답 방어 — 스트림이 종료됐는데 텍스트가 0 이면 전송 가치 없음.
        full_answer = "".join(full_answer_parts)
        if not full_answer.strip():
            logger.warning("LLM 빈 응답 (스트림 0청크)")
            yield {"data": json.dumps({"type": "error", "message": "LLM 이 빈 응답을 반환했습니다."}, ensure_ascii=False)}
            return

        # ─── 3. Citation faithfulness check ─────────────
        # 답변에 등장하는 (Roman, Arabic) 조항 태그가 검색된 citation 들과 일치하는지 검증.
        # 미검증 태그가 있으면 warning 으로 동봉 — UI 가 "검증되지 않은 인용" 배너로 표시.
        warnings = _check_citation_faithfulness(full_answer, citations)
        if warnings:
            logger.warning(f"[/ask] faithfulness 미검증 태그: {warnings}  (full_answer 첫 200자: {full_answer[:200]!r})")

        yield {"data": json.dumps({
            "type": "done",
            "citations": citations_payload,
            "escalation_needed": False,
            "faithfulness_warnings": warnings,
        }, ensure_ascii=False)}

    return EventSourceResponse(event_stream())


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


# ── Citation faithfulness check ────────────────────────────
#
# 시행지침 청크는 "Roman.Arabic" 위계 (예: II.3, III.7) 로 식별된다. LLM 이 답변에
# 인용을 적을 때 가장 흔한 hallucination 은 *이웃 태그 발명* — 검색 결과에 II-3 은
# 있는데 답변에 갑자기 "출처: II-7" 이라고 적는 식이다. 정확히 잡으려면 답변에서
# 추출한 (Roman, Arabic) 쌍이 검색된 citation 들의 (Roman, Arabic) 집합에 속하는지
# 확인하면 된다.

# 답변 본문에서 "II-3", "II.3", "II 3", "II-3 ⑤" 등 다양한 표기를 잡는 regex.
# - Roman: I/V/X 1~4 글자 (실제 시행지침은 IX 까지 사용)
# - 구분자: 하이픈, 점, 공백, 콜론
# - Arabic: 1~2 자릿수
# - 양옆에 단어 경계 — 영문 식별자 안에 있는 시퀀스 (예: VI-VIII) 는 매치 안 됨.
_TAG_RE = re.compile(
    r"(?<![A-Za-z가-힣])"     # 앞쪽: 영·한글 직후 아님 (단어 안 부분 매치 방지)
    r"([IVX]{1,4})"            # 그룹 1: Roman
    r"\s*[-.\s:]\s*"           # 구분자 (하이픈/점/공백/콜론, 양 옆 공백 OK)
    r"(\d{1,2})"               # 그룹 2: Arabic
    r"(?![\d.])"               # 뒤쪽: 추가 숫자·점 아님 (II.34 가 매치되지 않게)
)


def _extract_clause_pairs(text: str) -> set[tuple[str, str]]:
    """텍스트에서 모든 (Roman, Arabic) 조항 태그 쌍 추출."""
    return {(m.group(1), m.group(2)) for m in _TAG_RE.finditer(text or "")}


def _citation_to_pair(c) -> tuple[str, str] | None:
    """Citation 의 chapter+article 에서 (Roman, Arabic) 쌍 도출.

    Citation.chapter 예: "CHAPTER 1 > II. 기본직불금 지급대상 자격요건 등 주요 내용"
    Citation.article 예: "3. 소농직불 지급대상 자격요건"

    Roman 은 chapter 에서 "X." 패턴으로, Arabic 은 article 의 선두 "N." 으로 추출.
    실패하면 None — 이런 citation 은 faithfulness 비교 대상에서 제외 (False positive 방지).
    """
    rm = re.search(r"\b([IVX]{1,4})\.", c.chapter or "")
    am = re.match(r"^\s*(\d{1,2})\.", c.article or "")
    if rm and am:
        return (rm.group(1), am.group(1))
    return None


def _check_citation_faithfulness(answer: str, citations: list) -> list[str]:
    """답변에 등장하는 조항 태그 중 검색된 citation 으로 검증되지 않는 것의 리스트.

    빈 리스트 = 모든 인용이 검증됨 (또는 답변에 명시적 태그가 없음).

    설계 노트:
      - 답변에 태그가 전혀 없으면 false positive 0 (빈 리스트 반환).
      - citation 에 (Roman, Arabic) 쌍을 못 만든 경우는 비교 대상에서 빠짐 — UI 표시는
        과탐보다 미탐을 선호 (사용자에게 잘못된 경고를 띄우는 것이 더 해롭다).
      - 출력은 사람이 읽을 형태 ["II-7", "III-2", ...].
    """
    answer_pairs = _extract_clause_pairs(answer)
    if not answer_pairs:
        return []
    cited_pairs = set()
    for c in citations:
        p = _citation_to_pair(c)
        if p is not None:
            cited_pairs.add(p)
    unverified = sorted(answer_pairs - cited_pairs)
    return [f"{r}-{a}" for (r, a) in unverified]


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


# 사용자가 이미 공식 용어로 질문한 경우 LLM 재작성을 건너뛴다 (~500ms 절약).
# 정확히는 _expand_with_synonyms 가 이미 매핑하는 캐주얼 표현이 *없고*, 동시에
# 시행지침 본문 어휘 (TITLE_BOOST_KEYWORDS) 가 *최소 한 개* 포함되어 있을 때.
_FORMAL_TERMS = frozenset([
    "소농직불", "면적직불", "공익직불", "기본형", "지급대상", "자격요건",
    "지급단가", "농업경영체", "진흥지역", "역전구간", "재배면적", "준수사항",
    "화학비료", "영농기록", "영농폐기물", "공익기능", "행정처분", "감액지급",
    "부정수급", "농업·농촌", "종합소득금액", "환수", "등록취소",
])
_CASUAL_TRIGGERS = frozenset([
    "영농일지", "농사일지", "기록부", "비료", "처벌", "벌금", "취소",
    "교육이수", "직불", "직불금", "꼭", "써야", "받을", "걸리", "어떻게",
])


def _needs_rewrite(question: str) -> bool:
    """LLM rewrite 가 필요한지 결정. 이미 공식 어휘로 묻고 있으면 건너뛴다.

    조건:
      - casual trigger 단어가 하나라도 있으면 rewrite 필요.
      - 그렇지 않더라도 공식 용어가 전혀 없으면 (정말 vague 한 일상어 질의) rewrite 시도.
      - 공식 용어가 있고 casual trigger 도 없는 "이미 잘 쓰인 질의" 만 skip.
    """
    has_casual = any(c in question for c in _CASUAL_TRIGGERS)
    has_formal = any(f in question for f in _FORMAL_TERMS)
    if has_casual:
        return True
    if not has_formal:
        return True
    return False


async def _rewrite_query_for_rag(question: str) -> str:
    """검색 정확도를 위해 사용자 질문을 RAG 검색용 쿼리로 재작성한다.

    캐주얼 한국어 질문(예: "영농일지 꼭 써야함?") 을 시행지침에 등장할 가능성이 높은
    공식 용어(예: "영농기록 작성 의무 농업인 준수사항") 로 풀어 임베딩 검색의 신호를
    강화한다. 정적 시노님 사전(_expand_with_synonyms)이 못 잡는 표현 변이까지 커버.

    실패 모드:
      - LLM 호출 실패 시 원본 question 반환 (검색을 막지 않음)
      - 빈 응답 / 너무 긴 응답 → 원본 question 반환

    ★★★ TODO (학습 모드 — 사용자가 채워주세요): rewriting 프롬프트 ★★★

    `prompt` 변수의 내용을 작성하세요. 좋은 rewriting 프롬프트의 조건:
      1) 짧게 — 쿼리 한 줄만 출력하도록 강제 ("Rewrite this question as a search query, no explanation")
      2) 도메인 anchoring — "공익직불사업 시행지침" 이라는 문맥을 명시
      3) 키워드 보강 — 동의어·상위어를 1~2 개 추가하도록 유도 ("영농일지 → 영농기록")
      4) 문장 형태 X, 키워드 나열 형태 O — 임베딩 검색에 더 잘 맞음
      5) 출력 길이 제한 — 30~80 자 권장

    프롬프트 예시 시작점:
        prompt = (
            "당신은 한국 농림축산식품부 공익직불사업 시행지침 검색 도우미입니다.\\n"
            "사용자의 캐주얼한 질문을 시행지침 공식 용어 위주의 짧은 검색 쿼리로 다시 쓰세요.\\n"
            "규칙:\\n"
            "- 출력은 쿼리 한 줄, 30~80자\\n"
            "- 핵심 명사·법적 의무 표현(의무, 자격, 처분 등)을 포함\\n"
            "- 캐주얼 표현(영농일지, 비료, 처벌 등)은 공식 용어(영농기록, 화학비료, 행정처분)도 함께 나열\\n\\n"
            f"사용자 질문: {question}\\n"
            "검색 쿼리:"
        )

    위를 자유롭게 수정해 보세요. 좋은 rewriting 은 "영농일지 꼭 써야함?" 같은 질의를
    "영농기록 작성 의무 농업인 준수사항" 같은 키워드 묶음으로 변환합니다.
    """
    api_key = settings.LITELLM_API_KEY
    if not api_key:
        return question

    # ───── 연구 기반 query rewriting 프롬프트 ──────────────
    # 설계 원칙 (RAG 연구 문헌 정리):
    #   1) 구조화된 템플릿 (Elastic, LlamaIndex) — 자유 재작성보다 노이즈 적음
    #   2) 다중 전략 결합 — 의도 추출 + 엔티티 보강 + 정규화 + 노이즈 제거
    #   3) Few-shot exemplars (LlamaIndex Query Transform Cookbook) — 출력 형태의
    #      가장 강력한 anchor. 4개로 4가지 intent type (의무/처분/자격/금액) 커버.
    #   4) 추측 금지 (Stack AI 2026 RAG guide) — hallucinated scope 차단
    #   5) HyDE 정신 (arxiv 2305.14283) — 캐주얼 어휘를 본문 본 어휘로 정렬해
    #      vocabulary mismatch 해소
    prompt = (
        "당신은 한국 농림축산식품부 공익직불사업 시행지침 RAG 검색 도우미입니다.\n"
        "사용자의 캐주얼한 한국어 질문을 시행지침 본문 어휘로 재작성해 "
        "임베딩 검색의 어휘 불일치 (vocabulary mismatch) 를 해소하는 것이 목표입니다.\n"
        "\n"
        "# 출력 규격\n"
        "- 쿼리 한 줄, 30~90 자. 키워드 나열 형태 (조사·서술어 최소화).\n"
        "- 쿼리 외 다른 설명·주석·접두어를 절대 출력하지 마세요.\n"
        "\n"
        "# 재작성 규칙\n"
        "1. 의도 분류: 질문이 [의무/자격요건/지급단가·금액/금지·부정수급·처분/신청절차/예외] 중 무엇을 묻는지\n"
        "   식별해 해당 핵심 명사를 쿼리에 포함.\n"
        "2. 어휘 정규화 (사용자 표현 → 시행지침 공식 용어):\n"
        "   영농일지·농사일지·기록부 → 영농기록\n"
        "   비료·농약 남용 → 화학비료, 농약 사용 기준\n"
        "   처벌·벌금·취소 → 부정수급, 행정처분, 환수\n"
        "   교육 이수 → 농업·농촌 공익기능 증진 교육\n"
        "   소득 기준 → 종합소득금액\n"
        "   직불·직불금 → 기본형 공익직불, 소농직불, 면적직불\n"
        "3. 엔티티 보존: 면적(ha), 농지유형(논·밭·과수), 농업인유형(청년·후계·귀농), 진흥지역 여부 등\n"
        "   사용자가 명시한 상황은 그대로 유지.\n"
        "4. 추측 금지: 사용자가 묻지 않은 시나리오·조건을 발명하지 마세요.\n"
        "\n"
        "# 예시\n"
        "Q: 영농일지 꼭 써야함?\n"
        "쿼리: 영농기록 작성 보관 의무 농업인 준수사항\n"
        "\n"
        "Q: 청년농인데 부정수급 걸리면 어떻게 되나요?\n"
        "쿼리: 청년농업인 부정수급 행정처분 환수 등록취소 제재\n"
        "\n"
        "Q: 0.3ha 논 있는데 받을 수 있어?\n"
        "쿼리: 소농직불 0.3ha 논 면적 자격요건 지급대상\n"
        "\n"
        "Q: 비료 너무 많이 쓰면 어떻게 됨?\n"
        "쿼리: 화학비료 사용 기준 준수사항 감액지급 위반\n"
        "\n"
        f"Q: {question}\n"
        "쿼리:"
    )
    # ───── 작성 영역 끝 ────────────────────────────────────

    try:
        async with httpx.AsyncClient(
            http1=True, http2=False, timeout=httpx.Timeout(15.0, connect=5.0),
        ) as http_client:
            llm = ChatOpenAI(
                model=settings.SUBSIDY_LLM_MODEL,
                base_url=settings.LITELLM_URL,
                api_key=api_key,
                temperature=0.0,
                max_tokens=80,
                http_async_client=http_client,
            )
            response = await llm.ainvoke([HumanMessage(content=prompt)])
        rewritten = response.content if isinstance(response.content, str) else ""
        rewritten = rewritten.strip().strip('"\'').replace("\n", " ")
        # 안전장치: 빈 응답 / 너무 길면 원본 사용
        if not rewritten or len(rewritten) > 200:
            logger.info(f"[rewrite] 응답 부적합, 원본 사용 ({rewritten!r})")
            return question
        return rewritten
    except Exception as e:
        logger.warning(f"[rewrite] 실패 ({type(e).__name__}: {e}). 원본 질문 사용.")
        return question


def _build_chat_messages(
    system_prompt: str,
    history: list[ChatTurn],
    latest_user_prompt: str,
) -> list[BaseMessage]:
    """SystemMessage + 이전 대화 + 최신 사용자 메시지(프롬프트화된 인용 포함)을 조립.

    구조:
        [System(persona+rules),
         Human("이전 사용자 질문 1"), AI("그에 대한 답변 1"),
         Human("이전 사용자 질문 2"), AI("그에 대한 답변 2"),
         ...
         Human(latest_user_prompt)]   # ← 인용 조항 + 프로필 요약 + 신규 질문

    가장 최신 사용자 turn 만 RAG 인용 조항을 포함하는 풀 프롬프트로 들어간다.
    이전 turn 들은 raw 텍스트만 — citations 는 history 에 보존하지 않기 때문.

    ★★★ TODO (학습 모드 — 사용자가 채워주세요): history 절단 전략 ★★★

    이 자리에서 'history 의 어디까지 LLM 에 전달할지' 를 결정합니다.

    선택지 (각각 5~10 줄):
      A) "전부 보낸다" — history 그대로 BaseMessage 로 변환. 단순하지만 토큰비용이
         턴마다 누적. 시행지침 Q&A 는 보통 5~10 턴이면 끝나니 실용적으로 OK.
      B) "최근 N 턴만 보낸다" — 예: MAX_TURNS = 6. 오래된 컨텍스트는 잘리지만
         예측 가능한 토큰비용. 슬라이딩 윈도우.
      C) "토큰 예산 기반" — tiktoken 으로 누적 토큰 세서 budget 초과 시 앞에서부터
         drop. 가장 정확하지만 의존성 추가 + 모델별 tokenizer 매칭 필요.

    구현 힌트 (B 안 기준 예시):
        MAX_TURNS = 6
        recent = history[-MAX_TURNS:]
        for turn in recent:
            if turn.role == "user":
                messages.append(HumanMessage(content=turn.content))
            else:
                messages.append(AIMessage(content=turn.content))

    선택 가이드:
      - 데모/MVP 단계라면 B (슬라이딩 윈도우) 가 가장 합리적.
      - 시행지침이 길고 답변이 인용 위주라 압축에 민감하므로 A 도 충분히 OK.
      - 운영에서 비용 가시성이 중요해지면 그때 C 로 옮기면 됨.
    """
    # ───── LLM-side stateless (history 무시) ─────
    # 의도적 결정: 이 모델·프롬프트 조합에서는 어떤 형태의 대화 컨텍스트도 RAG 근거를
    # 흐리는 drift 를 만들었다. 따라서 LLM 호출 시점에는 history 를 전혀 주입하지 않고
    # 매 turn 을 독립적인 단일 질의로 처리한다.
    #
    # 사용자 경험은 그대로 — 프론트엔드 thread 가 시각적으로 대화를 유지하고,
    # 사용자는 입력창에서 자연스럽게 follow-up 을 이어갈 수 있다. 다만 follow-up 에
    # "그것/그럼" 같은 지시어를 쓰면 모델이 맥락을 모르므로 사용자가 명시적으로
    # 다시 풀어 묻게 된다. 이 비용보다 잘못된 인용을 내놓는 비용이 훨씬 크다.
    #
    # `history` 파라미터는 미래 재도입 (예: query rewriting 으로 latest_user_prompt
    # 자체를 standalone 으로 만든 뒤 history 없이 호출) 을 위해 시그니처에 보존.
    _ = history  # noqa: F841 — 의도적 미사용
    return [
        SystemMessage(content=system_prompt),
        HumanMessage(content=latest_user_prompt),
    ]


async def _stream_llm(messages: list[BaseMessage]):
    """공익직불 전용 LLM 스트리밍 호출 — 청크 단위로 텍스트 yield.

    LiteLLM 프록시 경유 (팀 API 사용량 통합 추적).

    설정 노트:
      - settings.LITELLM_API_KEY / settings.LITELLM_URL 사용
      - async with httpx.AsyncClient 로 커넥션 누수 방지
      - reasoning 파라미터는 전달하지 않는다 — gemma-4-31b-it 가 무시하고 토큰 예산
        잠식해 빈 응답 반환하는 케이스 회피.

    속도 튜닝:
      - max_tokens=500, temperature=0.2 (단일-shot 호출과 동일)

    Yields:
        텍스트 청크 (str). 호출자는 이어 붙여 전체 답변 구성.
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
            streaming=True,
        )
        async for chunk in llm.astream(messages):
            content = chunk.content
            if isinstance(content, str) and content:
                yield content
