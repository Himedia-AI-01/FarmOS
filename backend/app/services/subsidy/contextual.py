"""Anthropic Contextual Retrieval — 청크별 LLM 생성 맥락 prefix.

배경 (Anthropic, 2024): 표준 RAG 는 청크를 그 자체로만 임베딩한다. 그래서
"농업인은 영농기록을 작성·보관하여야 한다" 같은 청크가 전체 문서의 어느 맥락
(소농직불 자격 절차 vs 부정수급 조사 절차 vs 일반 의무) 에 속하는지 임베딩
공간에서 모호해진다. Anthropic 의 처방: 색인 시 LLM 으로 "이 청크가 전체
문서에서 어떤 역할을 하는가" 1-2 문장 맥락을 생성해 prepend → 임베딩.
보고된 효과: retrieval failure rate 49% 감소.

본 모듈은 두 가지를 제공:
    1) generate_contextual_prefix(): 단일 청크에 대한 prefix 생성 (LLM 호출).
    2) ContextualPrefixCache: chunk_id → prefix 디스크 캐시 (JSON).

비용:
    한 번 생성하면 ~수백 청크 × 80 토큰 ≈ 1¢ 미만 (gpt-4o-mini 기준).
    캐시 덕에 재인덱싱은 LLM 재호출 없이 즉시.

실패 모드:
    LLM 호출 실패 → 빈 문자열 반환. gov_rag 는 빈 prefix 면 기존 정적 breadcrumb 만
    사용 (graceful degradation).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.core.config import settings

if TYPE_CHECKING:
    from app.services.subsidy.chunker import Chunk

logger = logging.getLogger(__name__)

CACHE_PATH = Path("data/gov/contextual_prefix_cache.json")


# ── 캐시 ────────────────────────────────────────────────────


class ContextualPrefixCache:
    """JSON 파일 기반 chunk_id → prefix 캐시.

    재인덱싱(force_reindex) 을 자주 하므로 LLM 비용 절감을 위해 디스크 캐시 필수.
    파일은 git 에 커밋해도 무방 (작고 결정론적). 현재 .gitignore 정책에 맞춰 결정.
    """

    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
            logger.info(f"contextual prefix 캐시 로드: {len(self._data)} 엔트리")
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"캐시 파일 손상 — 무시하고 새로 시작: {e}")
            self._data = {}

    def get(self, chunk_id: str) -> str | None:
        return self._data.get(chunk_id)

    def set(self, chunk_id: str, prefix: str) -> None:
        self._data[chunk_id] = prefix

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"contextual prefix 캐시 저장: {len(self._data)} 엔트리 → {self.path}")


# ── 프롬프트 ────────────────────────────────────────────────


def _build_context_prompt(chunk: Chunk) -> str:
    """청크 맥락 생성용 프롬프트 조립.

    ★★★ TODO (학습 모드 — 사용자가 채워주세요): 맥락 생성 프롬프트 ★★★

    이 프롬프트가 얼마나 좋은가에 따라 retrieval 정확도가 직접 변합니다.

    좋은 맥락 prefix 의 특징 (Anthropic 가이드라인):
      1) 짧음 — 50~100 토큰. 길면 임베딩 신호가 묽어짐.
      2) 청크의 "주제/지원금 종류/의도(의무·자격·처분 등)" 를 명시.
      3) 추측 금지 — 본문에 없는 내용을 발명하지 않음.
      4) 키워드 포함 — 검색 쿼리에 자주 등장할 명사를 우선 노출.
      5) 출력 형식 강제 — "맥락:" 같은 prefix 없이 본문만.

    프롬프트 시작점 (자유롭게 수정):

        prompt = (
            "당신은 한국 농림축산식품부 공익직불사업 시행지침 색인 도우미입니다.\\n"
            "주어진 청크가 시행지침 전체에서 어떤 역할을 하는지, 검색 정확도 향상을 위한\\n"
            "1-2 문장 맥락 요약을 작성하세요.\\n\\n"
            "# 청크 위치\\n"
            f"{chunk.chapter} > {chunk.section} > {chunk.subsection_title}\\n\\n"
            "# 청크 본문\\n"
            f"{chunk.content[:2000]}\\n\\n"
            "# 작성 규칙\\n"
            "- 50~100자, 1-2문장\\n"
            "- 어떤 지원금 (소농직불·면적직불·공익직불 일반) 에 관한 청크인지 명시\\n"
            "- 어떤 의도 (의무·자격요건·지급단가·부정수급 처분·신청절차·예외) 의 정보인지 분류\\n"
            "- 핵심 명사 키워드 위주 (영농기록·청년농업인·종합소득금액 등)\\n"
            "- 본문에 없는 내용 추측 금지\\n"
            "- 출력은 요약문만. \\"맥락:\\" 같은 prefix 금지.\\n\\n"
            "요약:"
        )
        return prompt

    추가 팁:
      - 청크가 표/리스트면 "표 형식의 ~ 정보" 같은 자료형 정보를 넣으면 좋음.
      - 청크가 너무 짧으면 (< 100 자) 굳이 맥락 추가하지 않고 빈 문자열 반환해도 됨.
      - examples (few-shot) 을 추가하면 일관성 ↑ 하지만 토큰 비용 ↑.
    """
    # ───── 도메인 특화 contextual retrieval 프롬프트 ─────
    # 설계: Anthropic 공식 템플릿 (XML 태그 + "answer only" 지시) 을 베이스로,
    # 농림축산식품부 공익직불제 도메인 정보를 <document> 블록에 압축하고,
    # 4개의 few-shot example 로 출력 형식을 anchor.
    #
    # 출처:
    #   - Anthropic Contextual Retrieval (anthropic.com/news/contextual-retrieval)
    #   - 농림축산식품부 공익직불제 (mafra.go.kr/gong/2593/subview.do)
    #   - Together AI 가이드 (구체적 prompt construction 패턴)
    #
    # 도메인 anchor 들 (LLM 이 분류 시 참조하는 taxonomy):
    #   - 지원금 분류: 기본형 (소농직불금/면적직불금) vs 선택형
    #   - 의도 분류: 정의/자격요건/지급단가/의무/금지/부정수급/신청절차/예외/서식
    #   - 농업인 8대 의무 (경영체등록·영농기록·농약화학비료·폐기물·교육·공동체·농지유지·가축분뇨)
    #   - 부정수급 처분 단계 (환수·5배 추가징수·1차 3년/2차 5년/3차 8년 신청제한)
    #   - 농업인 유형 (일반·청년농업인·후계농업인·전업농업인·2030세대·신규신청자)
    prompt = (
        "<document>\n"
        "2026년도 기본형 공익직불사업 시행지침 — 농림축산식품부가 발간하는 공익직불제 운영 매뉴얼.\n"
        "\n"
        "지원금 종류:\n"
        "- 소농직불금: 정액 130만원, 면적·소득·경력 등 9개 자격요건 모두 충족 시.\n"
        "- 면적직불금: 면적 구간별 단가, 진흥지역/비진흥, 논/밭/과수에 따라 차등.\n"
        "\n"
        "핵심 주제 영역:\n"
        "- 자격요건: 농지 (면적·유형·진흥지역 여부), 농업인 (영농경력·농촌거주연수·종합소득금액·농가 구성원 합산소득).\n"
        "- 농업인 8대 준수사항: 농업경영체 등록 / 영농기록 작성·보관 / 농약·화학비료 사용기준 / "
        "영농폐기물 수거·처리 / 농업·농촌 공익기능 증진 교육 이수 / 마을공동체 활동 / "
        "농지 형상·기능 유지 / 가축분뇨 공공수역 배출 금지.\n"
        "- 부정수급 처분: 환수, 최대 5배 추가징수, 신청제한 (1차 3년·2차 5년·3차 8년), 신고포상금.\n"
        "- 지급 절차: 신청 → 자격검증 (정보분석·차단) → 농작업 직접수행 현장조사 → 마을 자격검증위원회 → 지급.\n"
        "- 감액지급: 준수사항 위반 시 단계별 감액 (위반 항목·횟수·고의성 기준).\n"
        "\n"
        "농업인 유형: 일반, 청년농업인, 후계농업인, 전업농업인, 2030세대, 신규신청자, 귀농인.\n"
        "</document>\n"
        "\n"
        "<chunk_location>\n"
        f"{chunk.chapter} > {chunk.section} > {chunk.subsection_title}\n"
        "</chunk_location>\n"
        "\n"
        "<chunk>\n"
        f"{chunk.content[:2500]}\n"
        "</chunk>\n"
        "\n"
        "위 청크가 시행지침 전체에서 어떤 역할을 하는지, 검색 정확도 향상을 위한 1~2 문장 맥락을 한국어로 작성하세요.\n"
        "\n"
        "# 작성 규칙\n"
        "- 50~120자, 1~2 문장. 핵심 키워드 위주.\n"
        "- 어떤 지원금 (소농직불·면적직불·공익직불 일반) 인지 식별.\n"
        "- 어떤 의도 (정의·자격요건·지급단가·지급액·의무·금지·부정수급 처분·신청절차·예외·서식·감액) 인지 분류.\n"
        "- 본문에 등장하는 핵심 명사 (영농기록·종합소득금액·진흥지역·역전구간·청년농업인·환수·행정처분·신청제한 등) 를 포함.\n"
        "- 본문에 없는 내용을 추측·발명하지 마세요.\n"
        "- 출력은 맥락 요약문 하나만. \"맥락:\", \"이 청크는~\", \"본 조항은~\" 같은 prefix 금지.\n"
        "\n"
        "# 예시\n"
        "<example_chunk>\n"
        "위치: CHAPTER 1 > II. 기본직불금 자격요건 > 3. 소농직불 지급대상 자격요건\n"
        "본문: 소농직불금 지급대상 자격을 갖추기 위해서는 다음 요건을 모두 충족하여야 한다. "
        "① 농지 면적 0.1~0.5ha. ② 영농 경력 3년 이상. ③ 농촌 거주 3년 이상. "
        "④ 농업경영체 등록 완료. ⑤ 신청자 개인의 농업 외 종합소득이 2,000만원 미만 ...\n"
        "</example_chunk>\n"
        "<example_summary>\n"
        "소농직불금 지급대상 자격요건 — 면적 (0.1~0.5ha), 영농경력 3년, 농촌거주 3년, 농업경영체 등록, 종합소득금액 상한 등 9개 충족 조건을 규정.\n"
        "</example_summary>\n"
        "\n"
        "<example_chunk>\n"
        "위치: CHAPTER 2 > II > 7. 부정수급 처분 기준\n"
        "본문: 거짓이나 그 밖의 부정한 방법으로 직불금을 받은 경우, 환수하고 1차 위반 시 3년, 2차 5년, "
        "3차 8년의 신청제한 처분을 부과한다. 지급액의 최대 5배까지 추가 징수가 가능하다 ...\n"
        "</example_chunk>\n"
        "<example_summary>\n"
        "부정수급 행정처분 — 환수, 신청제한 (1차 3년·2차 5년·3차 8년), 최대 5배 추가징수 등 단계별 처분 기준을 규정.\n"
        "</example_summary>\n"
        "\n"
        "<example_chunk>\n"
        "위치: CHAPTER 1 > III. 농업인 준수사항 > 5. 영농기록 작성·보관 의무\n"
        "본문: 농업인은 영농활동을 입증할 수 있는 영농기록을 작성·보관하여야 한다. 부정수급 의심자에 대한 "
        "조사 시 영농기록 또는 농자재 구입 영수증 등을 제출하여야 한다 ...\n"
        "</example_chunk>\n"
        "<example_summary>\n"
        "농업인 준수사항 — 영농기록 작성·보관 의무, 부정수급 조사 시 영농기록·농자재 영수증 제출 의무를 규정.\n"
        "</example_summary>\n"
        "\n"
        "<example_chunk>\n"
        "위치: CHAPTER 1 > IV. 지급단가 > 2. 면적직불금 지급단가표\n"
        "본문: 면적직불금 지급단가는 농지 유형 및 면적구간에 따라 차등 적용된다. 진흥지역 논 1구간 "
        "(2ha 이하) 215만원/ha, 진흥지역 밭 1구간 205만원/ha, 비진흥지역 논 1구간 178만원/ha ...\n"
        "</example_chunk>\n"
        "<example_summary>\n"
        "면적직불금 지급단가표 — 농지유형 (논·밭·과수)·진흥지역 여부·면적구간별 ha당 단가를 표 형식으로 제시.\n"
        "</example_summary>\n"
        "\n"
        "요약:"
    )
    # ───── 작성 영역 끝 ──────────────────────────────────
    return prompt


# ── 단일 청크 prefix 생성 ──────────────────────────────────


def generate_contextual_prefix(chunk: Chunk, cache: ContextualPrefixCache) -> str:
    """청크에 대한 contextual prefix 를 LLM 으로 생성. 캐시 우선.

    실패 시 빈 문자열 반환 — 호출자는 정적 breadcrumb 만으로 fallback 가능.
    """
    cached = cache.get(chunk.id)
    if cached is not None:
        return cached

    api_key = settings.LITELLM_API_KEY
    if not api_key:
        logger.warning("LITELLM_API_KEY 미설정 — contextual prefix 생성 건너뜀")
        return ""

    prompt = _build_context_prompt(chunk)
    try:
        # 동기 컨텍스트에서 호출되므로 sync invoke 사용 (인덱싱은 startup 때 to_thread 로 감싸짐).
        with httpx.Client(http1=True, http2=False, timeout=httpx.Timeout(30.0, connect=10.0)) as http_client:
            llm = ChatOpenAI(
                model=settings.SUBSIDY_LLM_MODEL,
                base_url=settings.LITELLM_URL,
                api_key=api_key,
                temperature=0.0,
                max_tokens=120,
                http_client=http_client,
            )
            response = llm.invoke([HumanMessage(content=prompt)])
        text = response.content if isinstance(response.content, str) else ""
        text = text.strip().strip('"\'').replace("\n", " ")
        # 안전장치: 너무 길거나 빈 응답은 cache 하지 말 것 (다음 인덱싱 때 다시 시도)
        if not text or len(text) > 400:
            logger.info(f"contextual prefix 부적합 ({chunk.id}): {text[:80]!r}")
            return ""
        cache.set(chunk.id, text)
        return text
    except Exception as e:
        logger.warning(f"contextual prefix 생성 실패 ({chunk.id}): {type(e).__name__}: {e}")
        return ""
