"""공익직불 시행지침 RAG — Hybrid (Dense + BM25) + Contextual Retrieval + Reranker.

아키텍처 (2026-04 업그레이드):
    1) Indexing
       chunk → LLM contextual prefix (Anthropic 기법)
             → Solar passage embedding (ChromaDB)
             → Kiwi 형태소 BM25 (in-memory)

    2) Search
       query → 시노님 확장 (영농일지→영농기록 등)
             → [a] Solar query embed → ChromaDB top 20  (dense)
                [b] Kiwi tokenize → BM25 top 20         (sparse)
             → Reciprocal Rank Fusion (k=60) → top 10
             → 섹션 타이틀 키워드 부스트
             → bge-reranker-v2-m3-ko cross-encoder → top_k
             → 소단원 dedup → Citation[]

핵심 설계 결정:
    - Hybrid retrieval (RRF): 한국어는 어휘 변이가 크고 (영농일지/영농기록), 법령
      문서는 정확한 키워드 매칭이 중요. 두 retriever 의 약점을 RRF 가 상쇄.
    - Contextual prefix: 청크가 전체 문서에서 어떤 역할인지 LLM 이 1-2 문장으로
      summarize 해 prepend. 임베딩 시 의미 disambiguation, BM25 시 추가 어휘 신호.
    - 리랭커는 *원본 query* 사용 (확장 query 가 아닌). cross-encoder 는 자연어 fluency
      신호도 쓰므로 시노님 확장으로 인한 keyword stuffing 이 점수 왜곡을 일으킬 수 있음.

컬렉션 명: "gov_subsidy" (기존 diagnosis/review 컬렉션과 격리)
contextual cache: data/gov/contextual_prefix_cache.json (재인덱싱 시 LLM 비용 0)

주의:
    - ChromaDB에 embedding_function 미주입 — passage/query 모델이 다르므로 수동
      pre-compute 후 embeddings=, query_embeddings= 로 넘김.
    - 리랭커는 첫 검색 시 초기화 (~2초). lifespan() 에서 prewarm.
    - BM25 는 in-memory. 서비스 재시작 시 첫 search 호출에 lazy 빌드 (~1초/300청크).
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import TYPE_CHECKING

from langchain_upstage import UpstageEmbeddings

from app.core.config import settings
from app.core.vectordb import get_client
from app.schemas.subsidy import Citation
from app.services.subsidy.bm25_index import BM25Index, reciprocal_rank_fuse
from app.services.subsidy.chunker import PLACEHOLDER_SECTION_LABELS
from app.services.subsidy.contextual import (
    ContextualPrefixCache,
    generate_contextual_prefix,
)

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

    from app.services.subsidy.chunker import Chunk

logger = logging.getLogger(__name__)

COLLECTION_NAME = "gov_subsidy"

# 섹션 타이틀 기반 부스트 키워드
# 쿼리에 이들 중 하나가 포함되고, 청크의 subsection_title에도 포함되면 부스트
TITLE_BOOST_KEYWORDS: list[str] = [
    "소농직불", "면적직불", "지급대상", "자격요건", "지급단가", "농지",
    "농업인", "부정수급", "농업경영체", "진흥지역", "역전구간",
    "재배면적", "준수사항", "농약", "화학비료", "교육", "영농폐기물",
    "영농기록", "농업·농촌", "공익기능", "행정처분", "감액지급",
    "정보화", "검증", "보조금", "지도", "감독",
]

TITLE_BOOST_SCORE = 0.08   # 1등급 유사도 약 0.7~0.9 대비 약 10% 가산
RERANKER_CANDIDATES = 10   # 재랭킹에 넘길 후보 수 (15→10: cross-encoder pair 수 33% 감소,
                           # ~150-300ms 단축. 키워드 부스트가 이미 top 후보를 잘 골라주므로
                           # recall 손실은 미미.)
DEFAULT_TOP_K = 5

# 사용자가 흔히 쓰는 캐주얼 표현 → 시행지침 본문의 공식 용어 매핑.
# 임베딩(Solar) 자체는 의미적으로 가깝게 처리하지만:
#   1) 키워드 부스트는 문자열 매칭이라 표현 차이를 직접 보정해야 함.
#   2) 짧은 일상어 쿼리는 임베딩만으로도 거리가 벌어질 수 있어, 공식 용어를
#      쿼리에 덧붙여 임베딩하면 코사인 유사도가 한 단계 올라감.
# 추가 시 반드시 시행지침 본문에 실제 등장하는 공식 용어로만 매핑할 것.
QUERY_SYNONYMS: dict[str, list[str]] = {
    "영농일지": ["영농기록"],
    "농사일지": ["영농기록"],
    "기록부": ["영농기록"],
    "교육이수": ["교육"],
    "비료": ["화학비료"],
    "처벌": ["부정수급", "행정처분"],
    "벌금": ["부정수급", "행정처분"],
    "취소": ["행정처분"],
}


def _expand_with_synonyms(query: str) -> str:
    """쿼리 안에 등장하는 캐주얼 표현을 공식 용어로 보강해 새 쿼리 문자열을 만든다.

    원본 쿼리는 보존하고 뒤에 매핑된 공식 용어들을 공백으로 연결해 붙인다.
    예: "영농일지 꼭 써야함?" → "영농일지 꼭 써야함? 영농기록"
    """
    extras: list[str] = []
    for casual, formals in QUERY_SYNONYMS.items():
        if casual in query:
            extras.extend(formals)
    if not extras:
        return query
    # 중복 제거하면서 입력 순서 유지
    seen: set[str] = set()
    deduped = [x for x in extras if not (x in seen or seen.add(x))]
    return f"{query} {' '.join(deduped)}"

# Solar Embedding은 4000 토큰 제한. 한글은 약 0.4 tok/char → ~10,000 char 상한이지만
# prefix + 안전마진 고려해 6,000자로 split. 의미 손실 최소화 위해 자연 경계(빈 줄)에서 분할.
MAX_EMBED_CHARS = 6_000


class GovSubsidyRAG:
    """공익직불 시행지침 RAG 서비스.

    사용 예:
        rag = GovSubsidyRAG()
        added = rag.index_chunks(chunks)       # 초기 1회
        hits = rag.search("소농직불금 자격이 뭐야?", top_k=5)
    """

    def __init__(self) -> None:
        if not settings.UPSTAGE_API_KEY:
            raise RuntimeError(
                "UPSTAGE_API_KEY가 설정되지 않았습니다. "
                ".env 파일에 키를 추가하세요 (https://console.upstage.ai)."
            )

        self.embeddings = UpstageEmbeddings(
            api_key=settings.UPSTAGE_API_KEY,
            model="solar-embedding-1-large",
        )
        # 빈 컬렉션으로 획득 (embedding_function 미주입 — 수동 pre-compute)
        client = get_client()
        self.collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        # Hybrid retrieval — sparse 절반. lazy build (첫 search 호출 시).
        self.bm25 = BM25Index()

    # ── 인덱싱 ──────────────────────────────────────────────

    def index_chunks(self, chunks: list["Chunk"], skip_existing: bool = True) -> int:
        """청크를 ChromaDB에 임베딩 저장한다.

        Args:
            chunks: chunker.build_chunks() 결과
            skip_existing: True면 이미 저장된 id는 건너뜀

        Returns:
            새로 추가된 청크 수
        """
        if not chunks:
            return 0

        existing_ids: set[str] = set()
        if skip_existing:
            got = self.collection.get()
            if got and got["ids"]:
                existing_ids = set(got["ids"])

        new_chunks = [c for c in chunks if c.id not in existing_ids]
        if not new_chunks:
            logger.info(f"청크 {len(chunks)}개 모두 인덱스됨, 건너뜀")
            return 0

        # Anthropic Contextual Retrieval: LLM 으로 청크별 맥락 prefix 생성.
        # 캐시 우선 — 재인덱싱 시 LLM 재호출 없음.
        ctx_cache = ContextualPrefixCache()
        logger.info(f"contextual prefix 생성 시작 ({len(new_chunks)}개 청크)...")
        contextual_prefixes: dict[str, str] = {}
        for c in new_chunks:
            ctx = generate_contextual_prefix(c, ctx_cache)
            contextual_prefixes[c.id] = ctx
        ctx_cache.save()

        # 계층 컨텍스트 prefix — 정적 breadcrumb + 동적 LLM contextual.
        # 임베딩 토큰 제한에 맞춰 큰 청크는 자연 경계에서 분할 (원본 id_partN 형태로 추적)
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict] = []

        for c in new_chunks:
            ctx_line = contextual_prefixes.get(c.id, "")
            ctx_section = f"맥락: {ctx_line}\n" if ctx_line else ""
            prefix = (
                f"[{c.chapter} > {c.section} > {c.subsection}]\n"
                f"이 구절은 {c.subsection_title} 관련 내용입니다.\n"
                f"{ctx_section}"
                f"\n"
            )
            # 임베딩 토큰 제한 대응 분할
            parts = _split_for_embedding(c.content, MAX_EMBED_CHARS - len(prefix))
            base_meta = {
                "chapter": c.chapter,
                "section": c.section,
                "subsection": c.subsection,
                "subsection_title": c.subsection_title,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "section_type": c.section_type,
                "contextual_prefix": ctx_line,  # 디버깅·BM25 토큰화에 도움
            }
            for i, part in enumerate(parts):
                part_id = c.id if len(parts) == 1 else f"{c.id}_p{i}"
                if part_id in existing_ids:
                    continue
                ids.append(part_id)
                documents.append(prefix + part)
                metadatas.append({**base_meta, "parent_id": c.id, "part": i, "part_total": len(parts)})

        if not ids:
            logger.info("인덱싱할 신규 분할 조각이 없습니다")
            # BM25 도 재빌드 (신규 청크 0이지만 기존 문서에 BM25 가 비어있을 수 있음)
            self._rebuild_bm25_from_collection()
            return 0

        # Solar passage 임베딩 — embed_documents 내부적으로 -passage 모델 사용
        logger.info(f"Solar passage 임베딩 호출 중 ({len(documents)}건)...")
        vectors = self.embeddings.embed_documents(documents)

        self.collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=vectors,
        )
        logger.info(f"인덱싱 완료: {len(ids)} 조각 추가 (소스 청크 {len(new_chunks)}개 기준, 누적 {self.count()}건)")

        # BM25 재빌드 — 새로 추가된 청크까지 포함.
        self._rebuild_bm25_from_collection()
        return len(ids)

    # ── BM25 빌드 (Hybrid 의 sparse 절반) ─────────────────

    def _rebuild_bm25_from_collection(self) -> None:
        """Chroma 의 모든 documents 를 가져와 BM25 인덱스 재구성.

        documents 는 contextual prefix + breadcrumb + 본문이 포함된 형태로 저장되어
        있다. BM25 토큰화는 형태소 단위라 prefix 도 자연스럽게 매칭 신호에 기여 (예:
        "영농기록" 이 contextual_prefix 에 있으면 "영농일지" 검색에는 도움 안 되지만
        "영농기록 작성" 검색에는 직접 매칭).
        """
        try:
            raw = self.collection.get(include=["documents"])
        except Exception as e:
            logger.warning(f"BM25 재빌드 실패 (collection.get): {e}")
            return
        ids = raw.get("ids") or []
        docs = raw.get("documents") or []
        if not ids or not docs:
            logger.info("BM25 재빌드 건너뜀: 컬렉션 비어있음")
            return
        self.bm25.build(ids, docs)

    def _ensure_bm25_built(self) -> None:
        """첫 search 호출 시 lazy 빌드 (서비스 재시작 후 첫 요청 보호)."""
        if self.bm25.is_built():
            return
        self._rebuild_bm25_from_collection()

    # ── 검색 ────────────────────────────────────────────────

    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list[Citation]:
        """쿼리에 가장 관련 높은 청크를 Citation 객체 리스트로 반환한다.

        Hybrid 파이프라인 (dense + sparse + rerank):
            0. 시노님 확장 — 캐주얼 → 공식 용어 보강
            1a. Dense:  Solar query 임베딩 → ChromaDB top 20
            1b. Sparse: Kiwi 토큰화 → BM25 top 20
            2. RRF 융합 → top RERANKER_CANDIDATES (10)
            3. 섹션 타이틀 키워드 부스트
            4. Cross-encoder 재랭킹 → top_k
            5. 소단원 dedup
        """
        if self.count() == 0:
            logger.warning("컬렉션이 비어있음 — 빈 결과 반환")
            return []

        # 0. 시노님 확장
        expanded = _expand_with_synonyms(query)
        if expanded != query:
            logger.info(f"쿼리 시노님 확장: {query!r} → {expanded!r}")

        # BM25 first-time build (서비스 재시작 후 첫 호출 보호)
        self._ensure_bm25_built()

        # 1a. Dense: Solar 쿼리 임베딩 + ChromaDB
        query_vec = self.embeddings.embed_query(expanded)
        n_dense = min(20, self.count())
        raw = self.collection.query(
            query_embeddings=[query_vec],
            n_results=n_dense,
        )
        dense_results = _format_chroma_results(raw)
        dense_ranking: list[str] = [c["id"] for c in dense_results]

        # 1b. Sparse: Kiwi + BM25
        bm25_pairs = self.bm25.search(expanded, top_k=20)
        bm25_ranking: list[str] = [cid for cid, _ in bm25_pairs]

        if not dense_ranking and not bm25_ranking:
            return []

        # 2. RRF 융합 — 두 랭킹의 순위만 사용해 융합
        fused_ids = reciprocal_rank_fuse(
            [dense_ranking, bm25_ranking],
            k=60,
            top_n=RERANKER_CANDIDATES,
        )
        logger.info(
            f"hybrid 융합: dense={len(dense_ranking)} + bm25={len(bm25_ranking)} "
            f"→ fused={len(fused_ids)}"
        )

        # 융합 결과의 doc/meta 를 한 번에 조회 (Chroma .get with ids)
        try:
            got = self.collection.get(ids=fused_ids, include=["documents", "metadatas"])
        except Exception as e:
            logger.warning(f"융합 결과 조회 실패: {e} — dense 단독 fallback")
            got = None

        # candidates: dense 결과의 score 를 보존하고 BM25-only 결과는 default score 부여
        if got and got.get("ids"):
            id_to_dense: dict[str, dict] = {c["id"]: c for c in dense_results}
            candidates: list[dict] = []
            for i, cid in enumerate(got["ids"]):
                if cid in id_to_dense:
                    candidates.append(id_to_dense[cid])
                    continue
                # BM25-only hit: dense score 가 없으므로 0.5 (중간) 부여 — 부스트·리랭커가 보정
                candidates.append({
                    "id": cid,
                    "document": got["documents"][i] if got.get("documents") else "",
                    "metadata": got["metadatas"][i] if got.get("metadatas") else {},
                    "score": 0.5,
                })
        else:
            # Chroma get 실패 → dense 결과만 사용 (graceful fallback)
            candidates = dense_results
        if not candidates:
            return []

        # 3. 섹션 타이틀 키워드 부스트
        query_kws = {kw for kw in TITLE_BOOST_KEYWORDS if kw in expanded}
        if query_kws:
            for c in candidates:
                title = c["metadata"].get("subsection_title", "")
                if any(kw in title for kw in query_kws):
                    c["score"] = round(c["score"] + TITLE_BOOST_SCORE, 4)
            candidates.sort(key=lambda x: x["score"], reverse=True)

        # 4. Cross-encoder 재랭킹 (원본 query 사용 — expanded 는 reranker 가 fluency 로 오인할 수 있음)
        top_candidates = candidates[:RERANKER_CANDIDATES]
        reranker = _get_reranker()
        pairs = [[query, c["document"]] for c in top_candidates]
        scores = reranker.predict(pairs)

        for c, s in zip(top_candidates, scores, strict=True):
            c["rerank_score"] = float(s)
        top_candidates.sort(key=lambda x: x["rerank_score"], reverse=True)

        # 5. 소단원 중복 제거 (split된 _p0/_p1 파트가 둘 다 top rank인 경우 1개만)
        seen_keys: set[tuple[str, str]] = set()
        unique_hits: list[dict] = []
        for h in top_candidates:
            meta = h["metadata"]
            key = (meta.get("chapter", ""), meta.get("subsection", ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique_hits.append(h)

        # 6. Citation으로 변환 (컨텍스트 prefix 제거 + 테이블 노이즈 정리)
        # max_chars=700: 400 은 한국어 법령 문장에서 핵심 의무·금지 표현이 잘리는
        # 사례가 있다 (예: "농업인은 ~를 작성·보관하여야 한다" 가 다른 문장 뒤에 위치).
        # 700 으로 두면 한 chunk 의 핵심 의무/예외 절을 모두 담는 데 충분.
        hits = unique_hits[:top_k]
        citations: list[Citation] = []
        for h in hits:
            meta = h["metadata"]
            raw = h["document"]
            # 인덱스 prefix 제거 ("[CHAPTER...]\n이 구절은...\n\n" 형태)
            if "\n\n" in raw:
                _, raw = raw.split("\n\n", 1)
            snippet = _clean_snippet_for_display(raw, max_chars=700)

            section = meta.get("section", "") or ""
            # 플레이스홀더 섹션 라벨은 UI 에 노출하지 않음
            # (chunker 내부 구현 디테일 — 단일 source-of-truth 는 chunker 모듈)
            if section in PLACEHOLDER_SECTION_LABELS:
                section = ""
            chapter_path = meta.get("chapter", "")
            if section:
                chapter_path = f"{chapter_path} > {section}"

            citations.append(Citation(
                article=meta.get("subsection", ""),
                chapter=chapter_path,
                snippet=snippet,
                similarity=h["rerank_score"],
            ))
        return citations

    # ── 빠른 검색 (리랭커 스킵) ─────────────────────────────

    def search_fast(self, query: str, top_k: int = DEFAULT_TOP_K) -> list[Citation]:
        """리랭커를 건너뛴 경량 검색 — Solar embedding + ChromaDB top_k + 타이틀 부스트만.

        용도: 짧은 자연어 쿼리에 대해 단순 발췌만 필요한 경우 (예: clause snippet 조회).
        리랭커 (~수 초) 를 건너뛰어 한 호출이 ~500ms 수준이 된다.

        품질 차이: cross-encoder 의 미세 재정렬을 잃지만, 카탈로그처럼 시행지침 본문과
        어휘가 가까운 짧은 쿼리에는 임베딩 코사인만으로 충분히 정확.
        """
        if self.count() == 0:
            return []

        query_vec = self.embeddings.embed_query(query)
        n_results = min(RERANKER_CANDIDATES, self.count())
        raw = self.collection.query(
            query_embeddings=[query_vec],
            n_results=n_results,
        )
        candidates = _format_chroma_results(raw)
        if not candidates:
            return []

        # 타이틀 키워드 부스트 (search() 와 동일 로직, 매우 저렴)
        query_kws = {kw for kw in TITLE_BOOST_KEYWORDS if kw in query}
        if query_kws:
            for c in candidates:
                title = c["metadata"].get("subsection_title", "")
                if any(kw in title for kw in query_kws):
                    c["score"] = round(c["score"] + TITLE_BOOST_SCORE, 4)
            candidates.sort(key=lambda x: x["score"], reverse=True)

        # 소단원 중복 제거 + Citation 변환 (search() 동일)
        seen_keys: set[tuple[str, str]] = set()
        unique: list[dict] = []
        for h in candidates:
            meta = h["metadata"]
            key = (meta.get("chapter", ""), meta.get("subsection", ""))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique.append(h)

        citations: list[Citation] = []
        for h in unique[:top_k]:
            meta = h["metadata"]
            raw_doc = h["document"]
            if "\n\n" in raw_doc:
                _, raw_doc = raw_doc.split("\n\n", 1)
            snippet = _clean_snippet_for_display(raw_doc, max_chars=400)

            section = meta.get("section", "") or ""
            if section in PLACEHOLDER_SECTION_LABELS:
                section = ""
            chapter_path = meta.get("chapter", "")
            if section:
                chapter_path = f"{chapter_path} > {section}"

            citations.append(Citation(
                article=meta.get("subsection", ""),
                chapter=chapter_path,
                snippet=snippet,
                similarity=h["score"],
            ))
        return citations

    # ── 조항 인덱스 (Roman.Arabic → 발췌) ────────────────────

    def get_clauses_index(self) -> dict[str, str]:
        """{"II-3" → "3 소농직불 지급대상 자격요건..." 전체 본문} 인덱스.

        - 키 형식: section 의 Roman ("II.") + subsection 의 Arabic ("3.") 조합.
        - 같은 (Roman, Arabic) 키로 여러 part 가 있으면 part 순서대로 이어붙인다.
        - 본문 첫 줄이 "[CHAPTER ... > ...]" 브레드크럼이면 제거한다.
        - 결과는 인스턴스 캐시(self._clauses_cache) — 첫 호출만 ChromaDB 스캔.

        실패 시 빈 dict 반환 (호출자가 graceful fallback 가능).
        """
        cached = getattr(self, "_clauses_cache", None)
        if cached is not None:
            return cached

        try:
            raw = self.collection.get(include=["metadatas", "documents"])
        except Exception as e:
            logger.warning(f"조항 인덱스 빌드 실패 (collection.get): {e}")
            self._clauses_cache = {}
            return self._clauses_cache

        docs: list[str] = raw.get("documents") or []
        metas: list[dict] = raw.get("metadatas") or []
        roman_re = re.compile(r"^([IVXLCDM]+)\.")
        arabic_re = re.compile(r"^(\d+)\.")
        breadcrumb_re = re.compile(r"^\[[^\]]*\]\s*\n", re.MULTILINE)

        groups: dict[str, list[tuple[int, str]]] = {}
        for doc, meta in zip(docs, metas):
            section = meta.get("section", "") or ""
            subsection = meta.get("subsection", "") or ""
            rm = roman_re.match(section)
            am = arabic_re.match(subsection)
            if not (rm and am):
                continue
            key = f"{rm.group(1)}-{am.group(1)}"
            try:
                part = int(meta.get("part", 0) or 0)
            except (ValueError, TypeError):
                part = 0
            cleaned = breadcrumb_re.sub("", doc, count=1).strip()
            groups.setdefault(key, []).append((part, cleaned))

        index = {
            key: "\n\n".join(d for _, d in sorted(parts))
            for key, parts in groups.items()
        }
        logger.info(f"조항 인덱스 빌드 완료: {len(index)}개 키 ({sorted(index.keys())[:8]}...)")
        self._clauses_cache = index
        return index

    # ── 유틸 ────────────────────────────────────────────────

    def count(self) -> int:
        return self.collection.count()

    def reset(self) -> None:
        """테스트/재인덱싱용 — 컬렉션 전체 삭제 + BM25 초기화."""
        client = get_client()
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        self.collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        # BM25 도 새 인스턴스 — index_chunks 가 다시 빌드함
        self.bm25 = BM25Index()
        # clauses_index 캐시도 무효화
        if hasattr(self, "_clauses_cache"):
            del self._clauses_cache


# ── 내부 헬퍼 ──────────────────────────────────────────────


def _split_for_embedding(text: str, max_chars: int) -> list[str]:
    """긴 본문을 자연 경계(빈 줄)에서 max_chars 이하로 분할.

    의미 손실 최소화를 위해 빈 줄 > 단일 줄바꿈 > 문자 단위 순으로 시도.
    """
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    # 1차 시도: 빈 줄 단위
    blocks = text.split("\n\n")
    current = ""
    for block in blocks:
        if not block.strip():
            continue
        if len(current) + len(block) + 2 > max_chars and current:
            parts.append(current.strip())
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current.strip():
        parts.append(current.strip())

    # 여전히 max_chars 초과하는 파트는 줄 단위로 재분할
    final: list[str] = []
    for p in parts:
        if len(p) <= max_chars:
            final.append(p)
            continue
        lines = p.split("\n")
        cur = ""
        for line in lines:
            if len(cur) + len(line) + 1 > max_chars and cur:
                final.append(cur.strip())
                cur = line
            else:
                cur = f"{cur}\n{line}" if cur else line
        if cur.strip():
            final.append(cur.strip())

    # 최후 수단: 문자 단위 강제 분할 (legal text에서는 거의 발생 안 함)
    truly_final: list[str] = []
    for p in final:
        if len(p) <= max_chars:
            truly_final.append(p)
        else:
            for i in range(0, len(p), max_chars):
                truly_final.append(p[i:i + max_chars])
    return truly_final


def _clean_snippet_for_display(text: str, max_chars: int = 400) -> str:
    """RAG citation snippet 을 UI 친화적으로 정리.

    - Markdown 테이블 행(| --- | ... |)을 제거 → 읽기 불가 pipe 노이즈 제거
    - 연속된 공백/줄바꿈을 단일 공백으로 축약
    - 선행 #, 하이픈, 별표 같은 Markdown 헤더·bullet 문자 정리
    - max_chars 로 자르되 가능하면 문장 끝에서 절단
    """
    import re as _re
    # 1) 테이블 구분자 행 제거 (| --- | --- |)
    text = _re.sub(r"\|\s*-{3,}\s*(?:\|\s*-{3,}\s*)+\|", " ", text)
    # 2) 테이블 본체 행을 좀 더 읽기 쉽게: "| a | b | c |" → "a, b, c"
    def _row_to_csv(m: _re.Match[str]) -> str:
        cells = [c.strip() for c in m.group(0).strip("|").split("|") if c.strip()]
        return " · ".join(cells)
    text = _re.sub(r"\|[^|\n]{1,80}(?:\|[^|\n]{1,80})+\|", _row_to_csv, text)
    # 3) Markdown 헤더·불릿·체크박스 문자 정리
    text = _re.sub(r"^#{1,6}\s*", "", text, flags=_re.MULTILINE)
    text = _re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)   # 이미지 placeholder
    text = text.replace("☑", "").replace("□", "")
    # 4) 잔여 pipe 문자 정리 (regex 가 못 잡은 짝 안맞는 | 구분자)
    text = _re.sub(r"\s*\|\s*", " · ", text)
    # 연속된 '·' 중복 제거
    text = _re.sub(r"(?:\s*·\s*){2,}", " · ", text)
    # 5) 공백 축약
    text = _re.sub(r"\s+", " ", text).strip()
    text = text.strip(" ·")

    if len(text) <= max_chars:
        return text
    # 문장 끝(마침표/물음표/느낌표 뒤 공백)에서 절단 시도
    cut = text[:max_chars]
    for sentinel in [". ", ".\u3000", "? ", "! "]:
        idx = cut.rfind(sentinel)
        if idx > max_chars * 0.6:
            return cut[: idx + 1] + "…"
    return cut.rstrip() + "…"


def _format_chroma_results(raw: dict) -> list[dict]:
    """ChromaDB query() 결과를 부스트 가능한 포맷으로 평탄화."""
    if not raw or not raw.get("ids") or not raw["ids"][0]:
        return []
    results: list[dict] = []
    for i, doc_id in enumerate(raw["ids"][0]):
        distance = raw["distances"][0][i] if raw.get("distances") else 0.0
        similarity = round(1 - distance, 4)
        results.append({
            "id": doc_id,
            "document": raw["documents"][0][i] if raw.get("documents") else "",
            "metadata": raw["metadatas"][0][i] if raw.get("metadatas") else {},
            "score": similarity,
        })
    return results


@lru_cache(maxsize=1)
def _get_reranker() -> "CrossEncoder":
    """Cross-encoder 리랭커 (최초 호출 시 모델 로드, 이후 캐시)."""
    from sentence_transformers import CrossEncoder

    logger.info(f"리랭커 로드 중: {settings.SUBSIDY_RERANKER_MODEL}")
    model = CrossEncoder(settings.SUBSIDY_RERANKER_MODEL, max_length=512)
    return model


# ── 초기 인덱싱 CLI (PDF 업데이트 시 재실행) ─────────────


def run_ingest_pipeline(force_reindex: bool = True) -> int:
    """시행지침 PDF → Markdown → chunk → contextual prefix → ChromaDB + BM25 인덱싱.

    초기 설치 시 1회, PDF 교체 시마다 재실행:
        cd backend && uv run subsidy-ingest

    파이프라인 단계:
        1) Markdown 로드 (캐시 우선, 없으면 Upstage Parse 호출)
        2) 청크 빌드 (chunker.py)
        3) 청크별 LLM contextual prefix 생성 (캐시 hit 시 즉시)
        4) Solar passage 임베딩 → ChromaDB 적재
        5) Kiwi 토큰화 → BM25 인덱스 빌드 (메모리)

    재인덱싱 가이드:
      - 정적 prefix 변경 (코드 수정) : force_reindex=True 충분.
      - 시행지침 PDF 교체 : force_reindex=True + contextual cache 삭제
        (data/gov/contextual_prefix_cache.json) 권장. 그렇지 않으면 chunk_id 가
        같은 청크 (예: II-3) 의 prefix 가 새 PDF 본문과 어긋날 수 있음.
      - contextual.py 의 프롬프트만 수정 : 캐시를 삭제해야 새 프롬프트로 재생성됨.

    Args:
        force_reindex: True면 기존 컬렉션 삭제 후 재인덱싱.

    Returns:
        인덱스된 벡터 수
    """
    import asyncio

    from app.services.subsidy.chunker import build_chunks, load_cached_markdown
    from app.services.subsidy.pdf_ingest import parse_subsidy_pdf

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        md = load_cached_markdown()
    except FileNotFoundError:
        md = asyncio.run(parse_subsidy_pdf())

    chunks = build_chunks(md)
    rag = GovSubsidyRAG()
    if force_reindex:
        rag.reset()
    added = rag.index_chunks(chunks)
    logger.info(
        f"인덱싱 완료: {added}개 벡터 추가 (총 {rag.count()}건). "
        f"BM25 빌드={rag.bm25.is_built()}"
    )
    return added


if __name__ == "__main__":
    run_ingest_pipeline()
