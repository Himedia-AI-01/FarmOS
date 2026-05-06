"""FarmOS Deep Agent 도구 정의 — 기존 서비스의 얇은 래퍼.

설계 원칙:
  - 비즈니스 로직 추가 금지. 모든 도구는 기존 service/core 함수의 얇은 래퍼.
  - DB·user_id 같은 런타임 의존성은 RunnableConfig.configurable 로 주입.
    (서브에이전트가 LLM 인자로 user_id를 다루지 않게 해 PII 누출 방지)
  - 도구는 항상 짧은 문자열·JSON을 반환. 거대한 dict 반환은 컨텍스트 오염.

안전 게이트 (verifier hard-gate):
  - `diagnose_pest` 출력에 농약·방제 정보가 포함되면 도구 내부에서 결정론적
    검증을 수행한다. LLM 검증자(verifier-agent)에 의존하지 않음 — 작은
    오케스트레이터 모델이 'verifier 호출 의무'를 무시해도 안전이 보장됨.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from sqlalchemy import select

from app.core.database import async_session
from app.core.journal_store import get_daily_summary, list_entries
from app.core.weather_client import get_weather
from app.models.ai_agent import AiAgentDecision
from app.models.user import User
from app.schemas.subsidy import Citation
from app.services.diagnosis_agent import run_diagnosis
from app.services.farm_agent.weather_alerts import (
    analyze_weather_risks,
    format_advisories_markdown,
)
from app.services.kamis import kamis_service
from app.services.kamis_aliases import matches_kamis_item, resolve_kamis_terms
from app.services.subsidy.tools import (
    check_eligibility_rule as _check_eligibility_rule,
    get_subsidy_details as _get_subsidy_details,
    get_user_profile as _get_user_profile,
    list_eligible_subsidies as _list_eligible_subsidies,
    search_subsidy_regulations as _search_subsidy_regulations,
    search_subsidy_regulations_fast as _search_subsidy_regulations_fast,
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


# ── 검증 게이트: 농약·방제 콘텐츠 결정론적 검사 ─────────────────────────────
#
# 설계: diagnose_pest 의 generate_diagnosis 노드는 LLM 으로 마크다운을 만든다.
# 작은 모델은 환각·반올림·생략을 일으킬 수 있다. 우리는 동일 파이프라인에서
# 흘러나온 원본 pesticide_data(JSON, fetch_pesticide 노드 출력)를 'authoritative
# truth' 로 사용해, LLM 출력의 모든 농약 주장(브랜드·희석·살포·시기)이 원본에
# 그대로 존재하는지 확인한다.
#
# 결과:
#   PASS    → 그대로 반환
#   FAIL    → 농약 섹션을 안전 폴백으로 대체 (RDA 페이지로 유도)
#   UNKNOWN → pesticide_data 가 비어있음 → 농약 추천 자체를 차단

_PESTICIDE_KEYWORDS_RE = re.compile(
    r"(농약|약제|방제\s*약|희석|희석배수|배\s*희석|살포|엽면|토양\s*혼화|경엽|"
    r"수화제|유제|액제|입제|성분|상표|제조사|수확\s*\d+\s*일\s*전)"
)
_DILUTION_RE = re.compile(r"\b\d{2,5}\s*배\b")
_HARVEST_INTERVAL_RE = re.compile(r"수확\s*\d+\s*일\s*전")


def _should_verify(text: str) -> bool:
    """LLM 출력에 검증이 필요한 농약·방제 콘텐츠가 들어있는지 판단.

    TODO(도메인 전문가): 한국 농약 표현·구어체에 맞게 본 함수를 정밀화하세요.
    현재 기준 (보수적):
      - 농약/방제 키워드 1개 이상 존재 OR
      - 희석 배수(예: "1000배") 패턴 OR
      - 수확 N일 전 안전사용기준 패턴
    고려할 추가 신호:
      - 특정 제품명 화이트리스트(스미치온, 다이센, 코퍼하이드록사이드 등)
      - "방제법", "약제 추천" 같은 사용자 의도 키워드
      - 일반 방제(예방·환기·물 빠짐)만 있는 경우는 검증 면제할지 여부
    """
    if not text:
        return False
    return bool(
        _PESTICIDE_KEYWORDS_RE.search(text)
        or _DILUTION_RE.search(text)
        or _HARVEST_INTERVAL_RE.search(text)
    )


def _extract_authoritative_strings(pesticide_data_json: str) -> dict[str, set[str]]:
    """fetch_pesticide 노드의 JSON 을 검증용 문자열 집합으로 변환.

    Returns:
        {
          "brand_names":  {"스미치온", ...},
          "ingredients":  {"페니트로치온", ...},
          "dilutions":    {"1000배", "500배", ...},  # 정규화된 형태
          "timings":      {"수확 7일 전까지", ...},
          "corporations": {"경농", ...},
        }
        파싱 실패 또는 빈 데이터면 모든 집합이 비어있는 dict 반환.
    """
    truth: dict[str, set[str]] = {
        "brand_names": set(),
        "ingredients": set(),
        "dilutions": set(),
        "timings": set(),
        "corporations": set(),
    }
    try:
        groups = json.loads(pesticide_data_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return truth
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        ing = (g.get("ingredient_name") or "").strip()
        if ing and ing != "성분정보없음":
            truth["ingredients"].add(ing)
        for p in g.get("products") or []:
            if not isinstance(p, dict):
                continue
            brand = (p.get("brand_name") or "").strip()
            if brand and brand != "상표명없음":
                truth["brand_names"].add(brand)
            corp = (p.get("corporation_name") or "").strip()
            if corp and corp != "제조사없음":
                truth["corporations"].add(corp)
            dilution = (p.get("dilution_text") or "").strip()
            if dilution and dilution != "정보없음":
                truth["dilutions"].add(dilution)
                # "1000배 희석" 류의 부분 매칭을 위해 숫자+배 패턴도 등록
                for m in _DILUTION_RE.findall(dilution):
                    truth["dilutions"].add(m.replace(" ", ""))
            timing = (p.get("application_timing") or "").strip()
            if timing and timing != "정보없음":
                truth["timings"].add(timing)
    return truth


def _verify_against_truth(
    text: str, truth: dict[str, set[str]]
) -> tuple[str, list[str]]:
    """LLM 출력의 농약 주장이 authoritative truth 에 포함되는지 검사.

    Returns:
        (status, mismatches)
          - status: "PASS" | "FAIL" | "UNKNOWN"
          - mismatches: FAIL 시 일치하지 않는 항목 목록 (로깅·디버깅용)
    """
    has_any_truth = any(truth[k] for k in truth)
    if not has_any_truth:
        return ("UNKNOWN", ["pesticide_data 비어있음 — 검증 불가"])

    mismatches: list[str] = []

    # 1) 희석배수: 텍스트의 모든 "Nxxx배" 가 truth.dilutions 에 존재해야 함
    for m in _DILUTION_RE.findall(text):
        normalized = m.replace(" ", "")
        if not any(normalized in d.replace(" ", "") for d in truth["dilutions"]):
            mismatches.append(f"희석배수 '{m}' 원본에 없음")

    # 2) 수확 N일 전: 텍스트의 모든 timing 패턴이 truth.timings 에 존재해야 함
    for m in _HARVEST_INTERVAL_RE.findall(text):
        if not any(m.replace(" ", "") in t.replace(" ", "") for t in truth["timings"]):
            mismatches.append(f"안전사용기준 '{m}' 원본에 없음")

    # 3) 브랜드명·성분명: 텍스트가 truth 외 새로운 제품을 도입했는지 확인하는 건
    #    어휘적으로 어렵다(고유명사 인식). 대신 'brand_names ∪ ingredients' 집합
    #    중 텍스트에 등장하는 게 하나라도 있는지 = 진짜 데이터 기반인지 sanity check.
    if truth["brand_names"] or truth["ingredients"]:
        cited = (truth["brand_names"] | truth["ingredients"])
        if not any(name in text for name in cited):
            mismatches.append("원본 브랜드/성분명이 본문에 인용되지 않음 — 환각 의심")

    return ("FAIL" if mismatches else "PASS", mismatches)


_FAIL_FALLBACK_NOTICE = (
    "\n\n---\n"
    "⚠️ **농약 정보 안전 검증 실패** — 위 일반 방제법까지만 안내드리며, "
    "구체적 약제·희석배수·살포 시기는 안전을 위해 본 답변에서 제외했습니다. "
    "정확한 약제 정보는 농촌진흥청 농약안전정보시스템(psis.rda.go.kr)에서 "
    "확인해주세요."
)

_UNKNOWN_FALLBACK_NOTICE = (
    "\n\n---\n"
    "ℹ️ 등록된 약제 데이터가 없어 농약 추천을 제공하지 못했습니다. "
    "지역 농업기술센터 또는 농촌진흥청 농약안전정보시스템(psis.rda.go.kr)에 "
    "문의해주세요."
)


def _strip_pesticide_sections(text: str) -> str:
    """검증 실패 시 농약·약제 섹션을 제거. 일반 방제·예방 정보는 유지.

    마크다운 헤더(##/###/####) 단위로 자르고, 헤더에 농약 키워드가 있는
    섹션은 통째로 드롭한다. 보수적: 헤더 없는 본문은 줄 단위로 농약 키워드
    포함 줄을 제거.
    """
    lines = text.splitlines()
    out: list[str] = []
    skip_section = False
    for line in lines:
        is_header = line.lstrip().startswith("#")
        if is_header:
            skip_section = bool(_PESTICIDE_KEYWORDS_RE.search(line))
            if not skip_section:
                out.append(line)
            continue
        if skip_section:
            continue
        # 헤더 없는 본문에서도 농약/희석 라인은 제거
        if _DILUTION_RE.search(line) or _HARVEST_INTERVAL_RE.search(line):
            continue
        out.append(line)
    cleaned = "\n".join(out).strip()
    return cleaned or "병해충 일반 방제 정보를 제공할 수 없었습니다."


@tool
async def diagnose_pest(pest: str, crop: str, region: str) -> str:
    """FarmOS 병해충 진단 파이프라인 실행 (날씨 + NCPMS + 농약 DB).

    Args:
        pest: 해충/병명 (예: "노균병", "진딧물")
        crop: 작물 (예: "토마토", "사과")
        region: 농장 지역 (예: "경북 영주시")

    Returns:
        진단 결과 마크다운 (권장 농약, NCPMS 방제법, 날씨 조언 포함).

    Safety (hard-gated):
        - 파이프라인 실패 시 절대 빈/가짜 응답을 돌려주지 않는다.
        - LLM 출력에 농약 콘텐츠가 포함되면 동일 파이프라인의 authoritative
          pesticide_data 와 결정론적으로 대조한다. 불일치 시 농약 섹션을
          제거하고 RDA 안내로 대체. 빈 truth 면 농약 추천 자체 차단.
        - 본 게이트는 오케스트레이터/서브에이전트의 LLM 동작과 무관하게 항상 작동.
    """
    final = ""
    pesticide_truth_json = ""
    completed_nodes: list[str] = []
    try:
        async for node, payload in run_diagnosis(pest=pest, crop=crop, region=region):
            completed_nodes.append(node)
            if node == "fetch_pesticide":
                pesticide_truth_json = payload.get("pesticide_data") or ""
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
        logger.warning(
            "diagnose_pest.no_result pest=%s crop=%s region=%s nodes=%s",
            pest, crop, region, completed_nodes,
        )
        return (
            "[진단_결과_없음] 도구가 결과 텍스트를 생성하지 못했습니다 "
            f"(통과 노드: {completed_nodes or '없음'}). "
            "농약·방제 정보를 임의로 생성하지 마세요. 사용자에게 데이터 부족을 안내하세요."
        )

    # ── Hard gate ────────────────────────────────────────────────────────
    if not _should_verify(final):
        # 농약 콘텐츠가 없으면 검증 면제 (예: "예방 위주로 환기 잘 해주세요")
        return final

    truth = _extract_authoritative_strings(pesticide_truth_json)
    status, mismatches = _verify_against_truth(final, truth)

    if status == "PASS":
        return final

    if status == "UNKNOWN":
        logger.warning(
            "diagnose_pest.verify_unknown pest=%s crop=%s region=%s reason=%s",
            pest, crop, region, mismatches,
        )
        stripped = _strip_pesticide_sections(final)
        return stripped + _UNKNOWN_FALLBACK_NOTICE

    # FAIL
    logger.warning(
        "diagnose_pest.verify_failed pest=%s crop=%s region=%s mismatches=%s",
        pest, crop, region, mismatches,
    )
    stripped = _strip_pesticide_sections(final)
    return stripped + _FAIL_FALLBACK_NOTICE


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
    """공익직불 시행지침에서 가장 관련 있는 조항을 정밀 검색 (느림).

    Solar embedding + BM25 + RRF + bge-reranker (수 초 소요). 정확도 최고지만 느리다.
    **단순 단일-주제 질의는 `search_subsidy_regulations_fast` 를 먼저 사용하고,
    결과가 약하면 본 도구로 재검색**하는 agentic RAG 패턴을 권장.

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
async def search_subsidy_regulations_fast(query: str, top_k: int = 5) -> str:
    """공익직불 시행지침 검색 (자동 escalation 내장).

    내부 동작:
      1) Fast 검색 (~500ms, 리랭커 스킵).
      2) 신뢰도 낮으면(top similarity < 0.5) 자동으로 full 정밀 검색(~수 초)으로 전환.

    호출자(에이전트)는 신뢰도 판단·재시도 결정을 **하지 마세요** — 본 도구가 처리.
    수동 정밀 검색이 필요한 경우(예: 사용자가 "완전한 조문" 요구)에만
    `search_subsidy_regulations` 직접 호출.

    **다중 주제는 한 응답에서 본 도구를 여러 번 호출** (병렬 실행됨).
    직렬 호출 금지.
    """
    import asyncio
    citations = await asyncio.to_thread(_search_subsidy_regulations_fast, query, top_k)
    if not citations:
        return "검색된 조항이 없습니다."
    return json.dumps(
        [c.model_dump(exclude_none=True) for c in citations],
        ensure_ascii=False,
    )


# ── 8대 준수사항 키워드 매트릭스 (deterministic shortcut) ─────────────────
#
# 농업인 8대 준수사항(공익직불법 시행령) 은 모두 "필수" 의무이며 위반 시 직불금
# 10% 감액. 가설 기반 RAG 만으로는 두 가설이 같은 청크를 회수할 때(예: 영농일지
# 질의에서 "VI. 영농기록 작성 및 보관" 청크가 양쪽에 동시 매칭) similarity 격차가
# 0.1 미만으로 떨어져 UNCLEAR 가 나오는 false-negative 가 발생한다.
#
# 8대 준수사항은 도메인 상수이므로 RAG 점수에 의존하지 않고 키워드로 단정한다.
# 키워드는 사용자 구어체와 시행지침 정식 용어를 모두 포함한다.
_MANDATORY_TOPIC_KEYWORDS: tuple[tuple[str, ...], ...] = (
    ("영농일지", "영농기록", "농사일지", "기록부", "작업일지"),       # ⑧ 기록·보관
    ("경영체 등록", "경영체등록", "농업경영체"),                       # ① 경영체 변경신고
    ("농지 형상", "농지형상", "농지 관리", "농지관리"),               # ② 농지 형상·기능 유지
    ("농약 안전사용", "농약안전사용", "안전사용기준"),                 # ③ 농약 안전사용
    ("화학비료", "비료 사용기준", "비료사용기준"),                     # ④ 화학비료 사용기준
    ("영농폐기물", "폐비닐", "농약병"),                                # ⑤ 폐기물 적정 처리
    ("공익기능", "공익기능 증진 교육", "교육 이수"),                   # ⑥ 의무교육 이수
    ("마을공동체", "마을 활동", "마을활동"),                           # ⑦ 마을 활동
)


def _matches_known_mandatory(topic: str) -> bool:
    """topic 이 8대 준수사항 중 하나에 해당하는지 키워드 매칭.

    8대 준수사항은 시행령에 의한 법적 의무이므로 RAG similarity 격차에 무관하게
    MANDATORY 로 단정해야 한다 (UNCLEAR 응답이 사용자에게 직불금 박탈 위험을
    잘못 전달하는 회귀 방지).
    """
    if not topic:
        return False
    t = topic.replace(" ", "")
    for group in _MANDATORY_TOPIC_KEYWORDS:
        if any(kw.replace(" ", "") in t for kw in group):
            return True
    return False


# 시행지침 본문에 등장하면 의무 조항임을 강하게 시사하는 법령 표현.
# "권장한다" 같은 표현은 임의 단서지만 시행지침 특성상 거의 등장하지 않는다.
_MANDATORY_PHRASES: tuple[str, ...] = (
    "하여야 한다",
    "해야 한다",
    "준수하여야",
    "준수해야",
    "준수사항",
    "위반 시",
    "위반시",
    "감액",
    "이행하여야",
    "이행해야",
)


def _has_mandatory_language(citations: list[Citation]) -> bool:
    """검색된 인용 본문에 의무 법령 표현이 등장하는지 확인 (top 2개 청크 검사)."""
    for c in citations[:2]:
        text = (c.snippet or "") + " " + (c.article or "") + " " + (c.chapter or "")
        if any(phrase in text for phrase in _MANDATORY_PHRASES):
            return True
    return False


@tool
async def search_subsidy_obligation_check(topic: str) -> str:
    """특정 의무가 강제인지 임의인지 판정 (가설 기반 RAG + 도메인 단정).

    "X 꼭 해야 해?", "X 안 해도 돼?" 같은 yes/no 의무성 질문 전용 도구.
    내부적으로 두 가설을 병렬 검색해 증거 강도를 비교한다 (2026 hypothesis-driven RAG):
      - 가설 A: "{topic} 의무 준수 위반 감액"  (의무 증거)
      - 가설 B: "{topic} 권장 임의 선택"      (선택 증거)

    Verdict 결정 우선순위 (false-negative 방지):
      1) topic 이 8대 준수사항 키워드와 매칭 → 즉시 MANDATORY (시행령 직접 근거).
      2) 의무 가설 검색 결과 본문에 "하여야 한다", "준수사항", "감액" 등 의무
         법령 표현 등장 + ob_top >= 0.4 → MANDATORY.
      3) 일반 유사도 격차 휴리스틱 (ob_top >= 0.5 AND ob_top - op_top >= 0.1).

    Args:
        topic: 의무 항목 (예: "영농기록 작성 보관", "공익기능 교육 이수").
    """
    import asyncio
    obligation_q = f"{topic} 의무 준수 위반 감액"
    optional_q = f"{topic} 권장 임의 선택"
    obligation_cites, optional_cites = await asyncio.gather(
        asyncio.to_thread(_search_subsidy_regulations_fast, obligation_q, 5),
        asyncio.to_thread(_search_subsidy_regulations_fast, optional_q, 5),
    )
    ob_top = max((c.similarity or 0.0 for c in obligation_cites), default=0.0)
    op_top = max((c.similarity or 0.0 for c in optional_cites), default=0.0)

    # Evidence-strength gate: extract topic head-words and check whether they
    # appear in the cited article TITLE (not just the snippet body). Adjacent
    # parent-category citations (snippet matches but title doesn't) are not
    # strong enough to claim MANDATORY — they're the over-classification trap.
    def _topic_in_article_title(cites) -> bool:
        # Use top-3 chars groups (Korean head-words) to test overlap.
        head_words = [w for w in topic.split() if len(w) >= 2][:3]
        if not head_words:
            return False
        for c in cites[:3]:
            article = (getattr(c, "article", "") or "")
            if any(hw in article for hw in head_words):
                return True
        return False

    title_match = _topic_in_article_title(obligation_cites)

    # 1) 8대 준수사항은 시행령상 의무 — RAG 점수 무관 단정.
    if _matches_known_mandatory(topic):
        verdict = "MANDATORY"
        verdict_source = "known_8_obligations"
    # 2) 의무 법령 표현이 본문에 직접 등장 + 인용 제목에도 주제 등장 → 단정.
    #    snippet match alone (no title match) = adjacent category → UNCLEAR.
    elif ob_top >= 0.4 and _has_mandatory_language(obligation_cites) and title_match:
        verdict = "MANDATORY"
        verdict_source = "mandatory_phrase_with_title_match"
    # 3) 일반 유사도 격차 휴리스틱 (title match 추가 요구).
    elif ob_top >= 0.5 and ob_top - op_top >= 0.1 and title_match:
        verdict = "MANDATORY"
        verdict_source = "similarity_margin_with_title_match"
    elif op_top >= 0.5 and op_top - ob_top >= 0.1:
        verdict = "OPTIONAL"
        verdict_source = "similarity_margin"
    else:
        # Adjacent-category match (snippet hit but no title) lands here.
        # Also: balanced or weak signals.
        verdict = "UNCLEAR"
        verdict_source = "no_signal" if ob_top < 0.4 else "adjacent_match_only"

    payload = {
        "topic": topic,
        "obligation_evidence": [c.model_dump(exclude_none=True) for c in obligation_cites],
        "optional_evidence": [c.model_dump(exclude_none=True) for c in optional_cites],
        "obligation_top_sim": round(ob_top, 4),
        "optional_top_sim": round(op_top, 4),
        "verdict_hint": verdict,
        "verdict_source": verdict_source,
    }
    return json.dumps(payload, ensure_ascii=False)


@tool
async def get_subsidy_details(subsidy_code: str) -> str:
    """지원금 코드로 상세 정보 조회 (카드/드로어 UI 데이터)."""
    async with async_session() as db:
        detail = await _get_subsidy_details(db, subsidy_code)
    if detail is None:
        return f"코드 {subsidy_code} 지원금을 찾을 수 없습니다."
    return detail.model_dump_json(exclude_none=True)


# ── 2.5. 알려진 RAG 공백 (시행지침 외 출처) ─────────────────────────────────
# 시행지침 RAG 가 답하지 못하는 자주 묻는 주제들을 운영자가 손으로 큐레이션해서
# 정답 + 출처 + 콜센터 안내를 제공한다. RAG 결과보다 신뢰성 높음 (시행지침 외 사업
# 지침서·법령에 근거).
#
# 매핑 키워드는 사용자 질문에 등장하는 핵심 토큰. 매칭되면 도구가 검색하지 않고
# 큐레이션 답변을 즉시 반환 → 에이전트는 그대로 사용자에게 전달.

_KNOWN_GAPS: dict[str, dict[str, str | list[str]]] = {
    "청년농 가산금": {
        "keywords": ["청년농 가산금", "청년농업인 가산"],
        "answer": (
            "청년농업인 가산금은 공익직불 시행지침이 아닌 별도 **청년농업인 영농정착지원사업** "
            "예산으로 운영됩니다.\n\n"
            "**2026년 단가 (참고):** 1년차 월 110만원, 2년차 월 100만원, 3년차 월 90만원 "
            "(최대 3년 지원).\n\n"
            "**자격:** 만 39세 이하, 영농경력 3년 이내, 농업경영체 등록.\n\n"
            "정확한 단가·자격은 농림축산식품부 청년농업인 콜센터 (1644-8778) 또는 "
            "관할 시·군 농업정책 담당 부서에 문의하시는 것이 가장 정확합니다."
        ),
        "source": "농림부 청년농업인 영농정착지원사업 운영지침",
    },
    "농외소득 한도": {
        "keywords": ["농외소득 한도", "농외 소득 얼마", "농외소득 얼마"],
        "answer": (
            "공익직불금 농외소득 기준은 두 가지입니다.\n\n"
            "**① 본인 농외소득:** 직전 연도 종합소득 **2,000만 원 미만**.\n"
            "**② 농가 합산 농외소득:** 가구 구성원 전체 합계 **4,500만 원 미만**.\n\n"
            "초과 시 공익직불금 지급 대상에서 제외됩니다. "
            "국세청 자료로 자동 검증되므로 신고 소득 기준입니다. "
            "[doc > 9. 공익직불사업의 정보화 및 검증]"
        ),
        "source": "공익직불 시행지침 II-3 / 9",
    },
    "변경등록 기한": {
        "keywords": ["변경등록 기한", "변경 등록 언제"],
        "answer": (
            "변경 사유 발생 후 **14일 이내**에 관할 읍·면·동에 변경신고하는 것이 원칙입니다. "
            "현장 여건 고려 시 매년 9월 30일까지 관할 읍·면·동에 변경 요청하면 등록정보를 "
            "현행화할 수 있습니다. 승계 처리는 매년 12월 31일까지 가능합니다. "
            "[doc > CHAPTER 1 > III. 사업추진 절차 > 4. 변경등록 및 신청 검증]"
        ),
        "source": "공익직불 시행지침 III-4",
    },
    "감액률 단계": {
        "keywords": ["감액률 단계", "반복 위반 감액", "감액 두 배"],
        "answer": (
            "공익직불 8대 준수사항 위반 시 감액률은 **1차 10% / 2차 20% / 3차 40%** "
            "단계별로 가중됩니다 (동일 항목 반복 위반 시).\n\n"
            "[doc > CHAPTER 1 > II. 6항 공익직불 준수사항]"
        ),
        "source": "공익직불 시행지침 II-6",
    },
}


def _check_known_gaps(query: str) -> dict | None:
    """Check if query matches a known RAG gap; return curated answer or None."""
    if not query:
        return None
    for gap_id, gap_data in _KNOWN_GAPS.items():
        if any(kw in query for kw in gap_data["keywords"]):
            logger.info("subsidy.known_gap_hit gap=%s", gap_id)
            return {
                "gap_id": gap_id,
                "answer": gap_data["answer"],
                "source": gap_data["source"],
                "verdict_hint": "OPTIONAL",  # 정보형 응답
            }
    return None


@tool
async def lookup_known_subsidy_gap(query: str) -> str:
    """시행지침 RAG 에 없는 자주 묻는 주제 (청년농 가산금·농외소득 한도 등) 의 큐레이션 답변.

    `search_subsidy_*` 호출 전에 본 도구를 먼저 호출하면 RAG miss 응답을 줄일 수 있다.
    매칭되지 않으면 빈 JSON 반환 — 그때 정상 RAG 검색 진행.
    """
    hit = _check_known_gaps(query)
    if hit is None:
        return json.dumps({"matched": False}, ensure_ascii=False)
    return json.dumps({"matched": True, **hit}, ensure_ascii=False)


# ── 3. 농장 데이터 (읽기 전용) ──────────────────────────────────────────────


@tool
async def get_current_weather() -> str:
    """KMA 초단기실황 + 예보. 사용자 농장 격자좌표 기반 (config FARM_NX/NY)."""
    data = await get_weather()
    return json.dumps(data, ensure_ascii=False, default=str)


@tool
async def get_weather_risk_advisory(config: RunnableConfig = None) -> str:
    """앞으로 5일치 기상 위험을 작물 맞춤형으로 결정론적 분석.

    LLM 임계 비교 없이 KMA 예보(`get_weather`) 의 daily_forecasts + current 를
    그대로 통과시켜 서리·폭염·강풍·호우·곰팡이병 호조 환경을 플래그한다.
    응답은 마크다운 한 블록 — 답변·브리핑에 그대로 인용 가능.

    사용 시점:
      - 사용자가 "내일 비 와도 괜찮을까?", "이번 주 작업 가능한 날?", "서리 위험?"
        같은 risk-aware 질문을 했을 때.
      - 농약 살포·드론 작업·관수 일정처럼 기상 임계가 결정적인 결정 지원.

    크롭 컨텍스트는 로그인 사용자 프로필의 main_crop 을 자동으로 사용한다 —
    별도 인자 없음. 비로그인 호출은 작물 무관 일반 advisory.
    """
    user_id = _user_id(config)
    main_crop: str | None = None
    if user_id:
        from sqlalchemy import select as _select

        try:
            async with async_session() as db:
                row = await db.execute(_select(User).where(User.id == user_id))
                user = row.scalar_one_or_none()
            if user is not None:
                main_crop = user.main_crop or None
        except Exception as exc:  # noqa: BLE001 — crop hint missing != tool failure
            logger.info("weather_risk.crop_lookup_failed user=%s err=%s", user_id, exc)

    weather_payload = await get_weather()
    advisories = analyze_weather_risks(weather_payload, main_crop=main_crop)
    return format_advisories_markdown(advisories, main_crop=main_crop)


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
async def get_market_prices_for_crop(crop: str) -> str:
    """특정 작물의 KAMIS 시세를 반환 (재배명 ↔ 유통명 alias 자동 처리).

    `get_market_prices` 와 달리 작물 단위 lookup 에 특화. FarmOS 가 사용하는
    재배 용어(예: "벼", "콩")가 KAMIS 의 유통 품목명(예: "쌀", "백태")과 다를 때
    `kamis_aliases` 매핑으로 자동 변환해 매칭한다.

    Args:
        crop: 사용자 또는 프로필이 사용하는 작물명 (예: "벼", "사과", "마늘").

    Returns:
        매칭된 KAMIS 행의 JSON 배열. 매칭 0건이면 빈 배열.
        매칭에 사용한 KAMIS 키워드도 함께 반환해 LLM 이 사용자에게 출처를 안내할 수 있게 함.
    """
    crop = (crop or "").strip()
    if not crop:
        return json.dumps({"matched_terms": [], "items": []}, ensure_ascii=False)

    matched_terms = resolve_kamis_terms(crop)
    items = await kamis_service.get_latest_prices()
    filtered = [it for it in items if matches_kamis_item(crop, it.get("item_name"))]
    return json.dumps(
        {"crop": crop, "matched_terms": matched_terms, "items": filtered[:20]},
        ensure_ascii=False,
        default=str,
    )


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
    lookup_known_subsidy_gap,  # RAG 외부 큐레이션 답변 (청년농 가산금·농외소득·변경등록 등)
    list_eligible_subsidies,
    check_eligibility_rule,
    search_subsidy_regulations_fast,
    # search_subsidy_regulations (slow rerank) 는 노출하지 않는다 — `_fast` 가
    # 내부적으로 신뢰도 낮을 때 자동 escalate 하므로 LLM 이 직접 호출할 이유가 없다.
    search_subsidy_obligation_check,
    get_subsidy_details,
]

FARM_DATA_TOOLS = [
    get_my_farm_profile,
    get_current_weather,
    get_weather_risk_advisory,
    get_market_prices,
    get_market_prices_for_crop,
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
    get_weather_risk_advisory,
    get_market_prices,
    get_market_prices_for_crop,
    list_journal_entries,
    get_journal_daily_summary,
    get_recent_iot_decisions,
    list_eligible_subsidies,
    check_eligibility_rule,
    search_subsidy_regulations_fast,
    # search_subsidy_regulations (slow) 제외 — `_fast` 의 자동 escalation 으로 대체.
    search_subsidy_obligation_check,
    get_subsidy_details,
]
