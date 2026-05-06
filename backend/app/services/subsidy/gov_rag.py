"""공익직불 시행지침 RAG — Backend-pluggable.

백엔드 (settings.SUBSIDY_RAG_BACKEND):
    "chroma" (default during cutover):
        section-level chunks → Solar passage embedding (ChromaDB)
                            → Kiwi BM25 (in-memory) → RRF → CrossEncoder → Citation[]
    "redis":
        leaf-level chunks (sub_split 가/나/다/①) → Redis Stack 8.4+ HASH
        → FT.HYBRID (BM25 + Solar dense, RRF/linear fusion server-side)
        → dedup-by-parent → Citation[]
        + Clause-lookup bypass: "II-3 알려줘" → get_clause_leaves(II-3) (no embed)

핵심 설계 결정:
    - Hybrid retrieval: 한국어는 어휘 변이가 크고 (영농일지/영농기록), 법령
      문서는 정확한 키워드 매칭이 중요. 두 retriever 의 약점을 융합이 상쇄.
    - Contextual prefix: 청크가 전체 문서에서 어떤 역할인지 LLM 이 1-2 문장으로
      요약해 prepend. 임베딩 시 의미 disambiguation, BM25 시 어휘 신호 보강.
      (Redis 백엔드: ctx:gov:<sha1(content)>, content-hash 키로 자동 무효화.)
    - Redis 백엔드는 leaf 단위 (가/나/다/① 분할, ~280 leaves) 인덱싱 — 하나의
      거대한 13K 청크 (II-8 행정처분 등) 가 LLM 입력에서 5~7K 토큰을 잡아먹던
      문제 해결.
    - CrossEncoder 리랭커는 chroma 백엔드에서만 사용. 100-Q 평가에서 fast 패스
      (rerank 없음) 가 hit@1 0.78, full (rerank) 0.55 로 역전 — 한국어 법령
      텍스트에서 multilingual 리랭커가 키워드 밀집 청크를 부적절하게 끌어올림.

컬렉션 명:
    chroma : "gov_subsidy"  (Chroma collection)
    redis  : "idx:gov_subsidy_v2" (FT index, prefix sub:gov:)

주의:
    - ChromaDB 에 embedding_function 미주입 — passage/query 모델이 다르므로 수동
      pre-compute 후 embeddings=, query_embeddings= 로 넘김.
    - BM25 는 in-memory (chroma). Redis 백엔드는 FT.HYBRID 의 BM25 substrate
      (text_ko, Kiwi pre-tokenized) 를 사용 — 서버 재시작 시 빌드 비용 0.
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
    # New mappings — surfaced by 100-Q eval failures.
    "다른 일":  ["농업외종합소득", "농업인 자격요건"],
    "농업외":   ["농업외종합소득"],
    "토해내":   ["환수", "환수금", "부정수급"],
    "환수금":   ["환수", "보조금", "행정처분"],
    "되돌려":   ["환수"],
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

        self.backend = (settings.SUBSIDY_RAG_BACKEND or "chroma").lower()
        if self.backend not in ("chroma", "redis"):
            logger.warning("unknown SUBSIDY_RAG_BACKEND=%r — falling back to chroma", self.backend)
            self.backend = "chroma"

        self.embeddings = UpstageEmbeddings(
            api_key=settings.UPSTAGE_API_KEY,
            model="solar-embedding-1-large",
        )

        # Chroma client + BM25 are constructed eagerly even when backend=redis
        # because (a) construction is cheap, (b) keeping the dual-backend code
        # path live during the cutover lets us A/B without a restart. Once
        # SUBSIDY_RAG_BACKEND=redis is the locked default, remove this block.
        client = get_client()
        self.collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self.bm25 = BM25Index()
        logger.info("gov_rag.init backend=%s", self.backend)

    # ── 인덱싱 ──────────────────────────────────────────────

    def index_chunks(self, chunks: list["Chunk"], skip_existing: bool = True) -> int:
        """청크를 백엔드 인덱스에 저장한다.

        chroma : 기존 ChromaDB 경로 (section-level chunks)
        redis  : sub_split_chunks 로 leaf 분할 후 Redis HASH + FT 인덱스에 적재.
                 Solar embeddings 와 contextual prefixes 는 모두 캐시 우선.
        """
        if not chunks:
            return 0

        if self.backend == "redis":
            return _run_async(_index_redis_async(chunks, skip_existing=skip_existing))

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

        백엔드 별 동작:
          redis  → 1) clause-lookup 정규식 일치 시 get_clause_leaves() 직접 호출
                    2) 아니면 FT.HYBRID (dense + BM25 server-side fusion)
                    3) dedup-by-parent → Citation
          chroma → Hybrid (dense + BM25) → RRF → 키워드 부스트 → CrossEncoder
        """
        if self.backend == "redis":
            return _run_async(_search_redis_async(query, top_k=top_k))

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
        """리랭커를 건너뛴 경량 검색.

        백엔드 별:
          redis  → search() 와 동일 (FT.HYBRID 자체가 이미 빠르고 rerank 없음).
          chroma → Solar embedding + ChromaDB top_k + 타이틀 부스트만.
        """
        if self.backend == "redis":
            return _run_async(_search_redis_async(query, top_k=top_k))

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

        백엔드 별:
          redis  → 모든 leaf 를 SCAN 한 후 clause_id 별 leaf_idx 순으로 join.
                   max_chars cap 없음 (이 함수는 진단/관리용 인덱스 — 일반 응답
                   경로는 redis_index.get_clause_leaves() 를 직접 사용해야 함).
          chroma → 기존 (Roman.Arabic 추출 → part 정렬 → 이어붙이기) 경로.

        실패 시 빈 dict 반환 (호출자가 graceful fallback 가능).
        """
        if self.backend == "redis":
            try:
                return _run_async(_clauses_index_redis_async())
            except Exception as e:  # noqa: BLE001
                logger.warning("clauses_index_redis_failed: %s", e)
                return {}

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
        if self.backend == "redis":
            try:
                from app.services.subsidy.redis_index import doc_count as _redis_doc_count
                return _run_async(_redis_doc_count())
            except Exception as e:  # noqa: BLE001 — fall back to 0
                logger.warning("redis_index.doc_count failed: %s", e)
                return 0
        return self.collection.count()

    def reset(self) -> None:
        """테스트/재인덱싱용 — 모든 인덱싱 상태를 비운다."""
        if self.backend == "redis":
            from app.services.subsidy.redis_index import drop_documents as _redis_drop
            _run_async(_redis_drop())
            if hasattr(self, "_clauses_cache"):
                del self._clauses_cache
            return

        client = get_client()
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        self.collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self.bm25 = BM25Index()
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


# ── Redis backend dispatch (settings.SUBSIDY_RAG_BACKEND="redis") ─────────
#
# These helpers mirror the chroma path's GovSubsidyRAG sync surface but call
# the async redis_index module under the hood. Sync callers reach us through
# asyncio.to_thread (api/subsidy.py, farm_agent/tools.py), so each call has
# its own thread without an active event loop — `asyncio.run` is safe here.

# Clause-lookup regex — fast bypass for "II-3 알려줘" / "II-3" / "C2-VI 조항".
# Matches a clause id at the start, end, or as a standalone token.
_CLAUSE_LOOKUP_RE = re.compile(
    r"(?:^|\s)((?:C2-)?[IVX]+(?:-\d+)?)(?:\s|$|[\s가-힣]*?(?:알려|조항|보여|뭐|뭔|부분))"
)


def _run_async(coro):
    """Run an async coroutine from a sync context.

    Contract:
        MUST be called from a thread with no running event loop (typically
        from ``asyncio.to_thread`` or a CLI/eval script). Calling from a
        coroutine on the FastAPI loop raises a RuntimeError so the misuse
        is loud rather than silently corrupting the connection pool.

    Pool ownership:
        If the global Redis pool is already initialised (FastAPI lifespan),
        we DO NOT touch init/close — closing it would tear down the pool
        used by other in-flight requests. We just run the coroutine on a
        fresh loop; redis-py 5+ tolerates this for one-shot commands.

        If the pool is not yet initialised (CLI / eval scripts), we init+
        close around the call so the script can use Redis without setup.
    """
    import asyncio

    from app.core.redis import close_redis, get_redis, init_redis

    # Fail loudly when invoked on a live event loop — corrupts the pool.
    try:
        asyncio.get_running_loop()
        raise RuntimeError(
            "_run_async called from a running event loop. "
            "Wrap the sync entry point in `await asyncio.to_thread(...)` "
            "or call the async API directly."
        )
    except RuntimeError as e:
        # `get_running_loop` raises RuntimeError when there is no loop —
        # that is the OK path. Any other RuntimeError is the one we raised.
        if "no running event loop" not in str(e):
            raise

    pool_already_initialised = get_redis() is not None

    async def _wrapped():
        if not pool_already_initialised:
            await init_redis()
        try:
            return await coro
        finally:
            if not pool_already_initialised:
                await close_redis()

    return asyncio.run(_wrapped())


def _try_clause_lookup(query: str) -> str | None:
    """Extract a clause id from query text. Returns 'II-3' / 'C2-VI' / None.

    Heuristics:
      - "II-3 알려줘" / "II-3" / "II-3 조항"   → "II-3"
      - "C2-VI"                                → "C2-VI"
      - Plain "II" alone is rejected (too ambiguous — return None).
      - Returns the FIRST match if multiple are present.
    """
    q = query.strip()
    # Direct clause-only inputs (most common chained-tool case)
    m = re.fullmatch(r"((?:C2-)?[IVX]+(?:-\d+))", q)
    if m:
        return m.group(1)
    # In-sentence: "II-3 알려줘", "지급단가 (II-4) 보여줘"
    m = _CLAUSE_LOOKUP_RE.search(q)
    if m:
        return m.group(1)
    return None


async def _index_redis_async(chunks: list, *, skip_existing: bool) -> int:
    """Redis 백엔드용 인덱싱.

    Pipeline:
        1. sub_split_chunks(chunks) → leaves
        2. ensure_index() (idempotent FT.CREATE)
        3. drop_documents() if not skip_existing
        4. concurrent contextual prefix generation (Haiku 4.5 via OpenRouter,
           cached in Redis so re-ingest is free)
        5. CachedSolarEmbeddings.aembed_documents() — pipeline-batched, cached
        6. index_leaves() — pipelined HSET writes

    Concurrency for prefix generation: semaphore=8. Empirically ~14 min for
    280 leaves cold; <30s after Redis cache is populated.
    """
    import asyncio

    from app.core.redis import init_redis
    from app.services.subsidy.chunker import sub_split_chunks
    from app.services.subsidy.contextual import (
        ContextualPrefixCache,
        generate_contextual_prefix,
    )
    from app.services.subsidy.embedding_cache import CachedSolarEmbeddings
    from app.services.subsidy.redis_index import (
        drop_documents,
        ensure_index,
        index_leaves,
    )

    # Make sure the connection pool exists (lifespan may not have run for CLI).
    await init_redis()

    leaves = sub_split_chunks(chunks)
    logger.info("redis_ingest.sub_split sections=%d → leaves=%d", len(chunks), len(leaves))

    await ensure_index(drop_existing=not skip_existing)
    if not skip_existing:
        await drop_documents()

    # ── Contextual prefixes (concurrent, capped) ──────────────────────────
    cache = ContextualPrefixCache()
    sem = asyncio.Semaphore(8)

    async def _gen_one(leaf) -> tuple[str, str]:
        async with sem:
            # generate_contextual_prefix is sync (HTTPS LLM call). Run in thread.
            prefix = await asyncio.to_thread(generate_contextual_prefix, leaf, cache)
            return leaf.id, prefix

    logger.info("redis_ingest.contextual_prefix.start n=%d concurrency=8", len(leaves))
    pairs = await asyncio.gather(*(_gen_one(L) for L in leaves))
    cache.save()  # no-op for redis backend; flushes JSON if that's the active backend
    prefixes: dict[str, str] = dict(pairs)
    n_filled = sum(1 for v in prefixes.values() if v)
    logger.info(
        "redis_ingest.contextual_prefix.done filled=%d/%d (cached or generated)",
        n_filled, len(leaves),
    )

    # ── Embeddings (cache-backed, batched) ────────────────────────────────
    embedder = CachedSolarEmbeddings()
    docs: list[str] = []
    for leaf in leaves:
        ctx = prefixes.get(leaf.id, "") or ""
        docs.append(f"{ctx}\n\n{leaf.content}" if ctx else leaf.content)

    logger.info("redis_ingest.embed.start n=%d", len(docs))
    vecs = await embedder.aembed_documents(docs)
    logger.info("redis_ingest.embed.done")

    # ── Index ─────────────────────────────────────────────────────────────
    n = await index_leaves(leaves, prefixes, vecs)
    logger.info("redis_ingest.indexed n=%d", n)
    return n


async def _search_redis_async(query: str, top_k: int) -> list[Citation]:
    """Redis 백엔드용 검색 — heuristic 기반, agentic capability 는 opt-in.

    Pipeline:
      1. Regex clause-lookup bypass (~1ms; "II-3 알려줘" → 직접 fetch).
      2. If planner OR CRAG enabled → run full agentic pipeline.
         (LLM-augmented; opt-in for accuracy-critical or multi-corpus deploys.)
      3. Else → heuristic pipeline (synonyms + boost + dedup, 0.89 hit@3, 113ms p50).
      4. Recursive cross-reference expansion (cheap, on by default) — augments
         the result of (2) or (3) with referenced 별표 N leaves.
    """
    from app.core.redis import init_redis

    await init_redis()

    # 1. Regex clause-lookup bypass — fast path for "II-3" / "C2-VI" patterns.
    clause_id = _try_clause_lookup(query)
    if clause_id:
        from app.services.subsidy.redis_index import get_clause_leaves
        leaves = await get_clause_leaves(clause_id, max_chars=3_000)
        if settings.SUBSIDY_RECURSIVE_REFS_ENABLED:
            from app.services.subsidy.agentic import expand_recursive_refs
            leaves = await expand_recursive_refs(leaves[:top_k])
        return [_leaf_to_citation(L, score=1.0) for L in leaves[:top_k + 2]]

    # 2. Full agentic path — only when planner or CRAG is explicitly enabled.
    if settings.SUBSIDY_PLANNER_ENABLED or settings.SUBSIDY_CRAG_ENABLED:
        from app.services.subsidy.agentic import run_agentic_search
        result = await run_agentic_search(query, top_k=top_k)
        return [
            _leaf_to_citation(L, score=L.get("score", 0.0))
            for L in result.leaves[:top_k]
        ]

    # 3. Default heuristic pipeline (proven 0.89 hit@3 / 113ms).
    cits = await _search_redis_heuristic(query, top_k=top_k)

    # 4. Recursive ref expansion — works on the heuristic-returned Citations.
    #    Skip if no leaves or feature off.
    if settings.SUBSIDY_RECURSIVE_REFS_ENABLED and cits:
        cits = await _expand_refs_for_citations(cits, top_k=top_k)

    return cits


async def _expand_refs_for_citations(
    cits: list[Citation], *, top_k: int,
) -> list[Citation]:
    """Apply recursive-ref expansion to a Citation list.

    The agentic.expand_recursive_refs operates on raw leaf-dicts. We translate
    Citations → leaf-dicts (synthetic; only the fields the expander touches),
    expand, then translate the additions back to Citations.
    """
    from app.services.subsidy.agentic import _extract_refs_from_leaves
    from app.services.subsidy.redis_index import get_clause_leaves

    leaves_view = [{"text": c.snippet or "", "clause_id": c.article} for c in cits]
    refs = _extract_refs_from_leaves(leaves_view)
    if not refs:
        return cits
    have = {c.article for c in cits}
    refs = [r for r in refs if r not in have][:3]
    extra: list[Citation] = []
    for cid in refs:
        try:
            extra_leaves = await get_clause_leaves(cid, max_chars=1500)
        except Exception:  # noqa: BLE001
            continue
        for L in extra_leaves[:2]:  # at most 2 leaves per referenced clause
            extra.append(_leaf_to_citation(L, score=0.0))
    if extra:
        logger.info("recursive_ref.expanded refs=%s added=%d", refs, len(extra))
    # Append; cap at top_k + extra slots so caller still sees originals first.
    return cits + extra


async def _search_redis_heuristic(query: str, top_k: int) -> list[Citation]:
    """Pre-agentic heuristic pipeline — kept for A/B comparison + emergencies.

    Synonym expansion + hybrid_search + title boost + soft dedup-by-parent.
    """
    from app.services.subsidy.redis_index import hybrid_search

    expanded = _expand_with_synonyms(query)
    raw_hits = await hybrid_search(expanded, k=max(top_k * 4, 20))
    if not raw_hits:
        return []

    query_kws = {kw for kw in TITLE_BOOST_KEYWORDS if kw in expanded}
    if query_kws:
        clause_keywords = _clause_keyword_map()
        for h in raw_hits:
            cid = h.get("clause_id") or ""
            if not cid:
                continue
            cid_kws = clause_keywords.get(cid, set())
            if cid_kws & query_kws:
                h["score"] = float(h.get("score", 0.0)) + TITLE_BOOST_SCORE
        raw_hits.sort(key=lambda x: x.get("score", 0.0), reverse=True)

    parent_count: dict[str, int] = {}
    unique: list[dict] = []
    for h in raw_hits:
        pid = h.get("parent_id") or ""
        n = parent_count.get(pid, 0)
        if n >= 2:
            continue
        parent_count[pid] = n + 1
        unique.append(h)
        if len(unique) >= top_k:
            break

    return [_leaf_to_citation(h, score=h.get("score", 0.0)) for h in unique[:top_k]]


@lru_cache(maxsize=1)
def _clause_keyword_map() -> dict[str, set[str]]:
    """clause_id → set of TITLE_BOOST_KEYWORDS that semantically match it.

    Hand-curated mapping mirroring the chroma path's subsection_title check.
    Compact because the clause set is small (~20 ids) and stable.
    """
    m: dict[str, set[str]] = {
        "I":      {"공익기능", "농업·농촌"},                         # 사업개요
        "II-1":   {"농지", "지급대상", "자격요건", "진흥지역"},
        "II-2":   {"농업인", "지급대상", "자격요건", "농업경영체"},
        "II-3":   {"소농직불", "자격요건", "역전구간", "지급대상"},
        "II-4":   {"지급단가", "면적직불", "진흥지역"},
        "II-5":   {"재배면적"},
        "II-6":   {"준수사항", "공익기능", "농약", "화학비료", "교육",
                   "영농폐기물", "영농기록", "감액지급"},
        "II-7":   {"부정수급"},
        "II-8":   {"부정수급", "행정처분", "감액지급"},
        "II-9":   {"정보화", "검증"},
        "II-10":  {"보조금"},
        "II-11":  {"지도", "감독"},
        "III-2":  {"지급대상", "농업경영체"},
        "C2-I":   {"농지", "준수사항"},
        "C2-II":  {"농약", "준수사항"},
        "C2-III": {"화학비료", "준수사항"},
        "C2-IV":  {"교육", "공익기능", "준수사항"},
        "C2-V":   {"영농폐기물", "준수사항"},
        "C2-VI":  {"영농기록", "준수사항"},
        "C2-VII": {"농업경영체", "준수사항"},
        "C2-VIII": {"준수사항"},
    }
    return m


def _leaf_to_citation(leaf: dict, score: float) -> Citation:
    """Map a redis_index hit (dict) to a Citation."""
    text = leaf.get("text", "") or ""
    # Strip the contextual prefix (text starts with "ctx_prefix\n\n<content>").
    # _leaf_to_citation is called from search and clause-lookup paths, both
    # want the user-visible body, not the prefix.
    if "\n\n" in text:
        _, body = text.split("\n\n", 1)
    else:
        body = text
    snippet = _clean_snippet_for_display(body, max_chars=700)

    clause_id = leaf.get("clause_id") or ""
    chapter_path = clause_id  # Redis path uses clause_id as the chapter label
    return Citation(
        article=clause_id or (leaf.get("sub_path") or ""),
        chapter=chapter_path,
        snippet=snippet,
        similarity=float(score),
    )


async def _clauses_index_redis_async() -> dict[str, str]:
    """Build the same {clause_id → joined content} dict the chroma path returned.

    For the Redis path we just call get_clause_leaves() per known clause and
    join the bodies. This function is mostly used by management/diagnostic
    tools — production answer paths should call get_clause_leaves() directly
    so they can cap by max_chars.
    """
    from app.core.redis import init_redis, require_redis
    from app.services.subsidy.redis_index import INDEX_NAME

    await init_redis()
    rb = require_redis()

    # Discover all clause_ids present in the index. FT.AGGREGATE w/ TAG GROUPBY
    # would be cleanest but we're tiny — just SCAN + extract from the index.
    cmd = [
        "FT.SEARCH", INDEX_NAME, "*",
        "RETURN", "1", "clause_id",
        "LIMIT", "0", "1000",
    ]
    raw = await rb.execute_command(*cmd)
    clause_ids: set[str] = set()
    for i in range(2, len(raw), 2):
        fields = raw[i]
        if not isinstance(fields, list):
            continue
        for j in range(0, len(fields) - 1, 2):
            if str(fields[j]) == "clause_id" and fields[j + 1]:
                clause_ids.add(str(fields[j + 1]))

    out: dict[str, str] = {}
    from app.services.subsidy.redis_index import get_clause_leaves
    for cid in clause_ids:
        leaves = await get_clause_leaves(cid, max_chars=999_999)  # full content for diagnostics
        joined_parts: list[str] = []
        for L in leaves:
            text = L.get("text", "") or ""
            if "\n\n" in text:
                _, body = text.split("\n\n", 1)
            else:
                body = text
            joined_parts.append(body.strip())
        out[cid] = "\n\n".join(joined_parts)
    return out


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
    logger.info("backend=%s force_reindex=%s sections=%d", rag.backend, force_reindex, len(chunks))
    if force_reindex:
        rag.reset()
    added = rag.index_chunks(chunks)
    extra = "" if rag.backend == "redis" else f" BM25 빌드={rag.bm25.is_built()}"
    logger.info("인덱싱 완료: %d 추가 (총 %d건).%s", added, rag.count(), extra)
    return added


if __name__ == "__main__":
    run_ingest_pipeline()
