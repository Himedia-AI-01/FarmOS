"""한국어 BM25 인덱스 — Hybrid retrieval 의 sparse 절반.

설계 원칙:
    Solar 임베딩(dense) 만으로는 vocabulary mismatch 가 자주 발생.
    예: 사용자 "영농일지" vs 본문 "영농기록", 사용자 "II-3" vs 본문 "3. 소농직불".
    BM25 는 명시적 단어 일치에 강하므로 dense 와 상호보완적.
    두 결과를 RRF (Reciprocal Rank Fusion) 로 융합해 reranker 입력으로 넘김.

토큰화:
    Kiwi (kiwipiepy) — 순 Python 휠 제공, Windows/Mac/Linux 동일 작동.
    명사(N*), 동사 어간(VV/VX), 형용사 어간(VA), 외래어(SL) 만 추출.
    조사·어미를 제거해 BM25 의 IDF 계산이 의미어에만 집중.

저장 전략:
    프로세스 메모리에만 보관. ChromaDB 가 가진 documents 로부터 시작 시 1회 빌드.
    ~300 청크 기준 빌드 ≤ 1초이므로 별도 디스크 직렬화 불필요.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kiwipiepy import Kiwi
    from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)


# Kiwi 가 의미어로 분류하는 POS 접두사 (조사/어미/기호 제외).
# 짧은 1글자 토큰은 IDF 가 흐려지므로 길이 ≥ 2 만 채택.
_KEEP_POS_PREFIXES = ("N", "VV", "VA", "VX", "SL", "SH", "SN")
_MIN_TOKEN_LEN = 2


# Module-level Kiwi singleton — shared by BM25Index instances and the standalone
# tokenizer used by redis_index.py (Korean BM25 substrate for FT.HYBRID).
_kiwi_singleton = None  # type: ignore[var-annotated]


def _get_kiwi_module():
    """Lazy-init the module-level Kiwi instance. ~200ms first call, then free."""
    global _kiwi_singleton
    if _kiwi_singleton is None:
        from kiwipiepy import Kiwi
        logger.info("Kiwi 형태소 분석기 로드 (module-level)")
        _kiwi_singleton = Kiwi()
    return _kiwi_singleton


def kiwi_tokenize(text: str) -> list[str]:
    """한국어 텍스트 → BM25용 의미어 토큰 리스트.

    Standalone version of BM25Index.tokenize() for the Redis FT pipeline.
    Both paths use the same Kiwi POS rules so dense/sparse signals match.
    """
    if not text or not text.strip():
        return []
    kiwi = _get_kiwi_module()
    result = kiwi.analyze(text, top_n=1)
    if not result:
        return []
    tokens: list[str] = []
    for token in result[0][0]:
        lemma = token.form
        pos = token.tag
        if not pos.startswith(_KEEP_POS_PREFIXES):
            continue
        if len(lemma) < _MIN_TOKEN_LEN:
            continue
        tokens.append(lemma)
    return tokens


# Backwards-compat alias used by redis_index.py imports written before the rename.
_kiwi_tokenize = kiwi_tokenize


class BM25Index:
    """프로세스 단위 한국어 BM25 인덱스.

    수명주기:
        1) GovSubsidyRAG 가 첫 search() 시 ensure_built() 호출 → ChromaDB 의 모든
           documents 를 가져와 토큰화 → BM25Okapi 빌드.
        2) index_chunks() 직후에도 ensure_built(force=True) 로 재빌드.
        3) search(query, top_k) 로 (chunk_id, score) 리스트 반환.
    """

    def __init__(self) -> None:
        self._kiwi: Kiwi | None = None
        self._bm25: BM25Okapi | None = None
        self._chunk_ids: list[str] = []
        self._lock = threading.Lock()

    # ── 토큰화 ────────────────────────────────────────────

    def _get_kiwi(self) -> Kiwi:
        """Kiwi 인스턴스 lazy 초기화. 모델 로드는 ~수백 ms 라 첫 호출만 비용."""
        if self._kiwi is None:
            from kiwipiepy import Kiwi
            logger.info("Kiwi 형태소 분석기 로드")
            self._kiwi = Kiwi()
        return self._kiwi

    def tokenize(self, text: str) -> list[str]:
        """한국어 텍스트를 BM25용 토큰 리스트로 분해.

        Kiwi 의 analyze() 는 (lemma, pos, start, length) 튜플 시퀀스를 반환한다.
        의미어 POS 만 채택하고 표제어(lemma)를 사용해 활용형 차이를 흡수.
        """
        if not text or not text.strip():
            return []
        kiwi = self._get_kiwi()
        result = kiwi.analyze(text, top_n=1)
        if not result:
            return []
        tokens: list[str] = []
        for token in result[0][0]:
            lemma = token.form
            pos = token.tag
            if not pos.startswith(_KEEP_POS_PREFIXES):
                continue
            if len(lemma) < _MIN_TOKEN_LEN:
                continue
            tokens.append(lemma)
        return tokens

    # ── 인덱싱 ────────────────────────────────────────────

    def build(self, chunk_ids: list[str], documents: list[str]) -> None:
        """전체 청크를 BM25 인덱스로 빌드 (in-place 교체).

        chunk_ids 와 documents 는 같은 길이·동일 순서여야 한다.
        """
        if len(chunk_ids) != len(documents):
            raise ValueError(
                f"chunk_ids({len(chunk_ids)}) 와 documents({len(documents)}) 길이 불일치"
            )
        with self._lock:
            from rank_bm25 import BM25Okapi
            tokenized: list[list[str]] = []
            empty_count = 0
            for doc in documents:
                toks = self.tokenize(doc)
                if not toks:
                    # 빈 토큰열은 BM25Okapi 가 IDF 계산에서 div-by-zero 를 낼 수 있으므로
                    # 단일 placeholder 로 보호. 어차피 어떤 쿼리와도 매칭되지 않음.
                    toks = ["__empty__"]
                    empty_count += 1
                tokenized.append(toks)
            if empty_count:
                logger.warning(f"BM25 빌드: {empty_count} 개 청크의 토큰열이 비어있음 (placeholder 처리)")
            self._bm25 = BM25Okapi(tokenized)
            self._chunk_ids = list(chunk_ids)
            logger.info(f"BM25 인덱스 빌드 완료: {len(chunk_ids)} 청크")

    def is_built(self) -> bool:
        return self._bm25 is not None and bool(self._chunk_ids)

    # ── 검색 ──────────────────────────────────────────────

    def search(self, query: str, top_k: int = 30) -> list[tuple[str, float]]:
        """쿼리 → BM25 top_k → [(chunk_id, score)] (score 내림차순).

        BM25 score 는 절대값 비교에 큰 의미가 없고 RRF 융합 시 순위만 사용되므로
        호출자는 score 무시하고 순서만 써도 된다.
        """
        if not self.is_built():
            return []
        tokens = self.tokenize(query)
        if not tokens:
            return []
        # mypy 가 _bm25 의 None 가능성을 추적 못하므로 assert
        assert self._bm25 is not None
        scores = self._bm25.get_scores(tokens)
        # numpy array 를 list of tuples 로 정리
        ranked = sorted(
            zip(self._chunk_ids, scores.tolist(), strict=True),
            key=lambda x: x[1],
            reverse=True,
        )
        # 0 점 (매칭 0 개) 컷오프 — 노이즈 제거
        return [(cid, s) for cid, s in ranked[:top_k] if s > 0]


# ── RRF 융합 ────────────────────────────────────────────────


def reciprocal_rank_fuse(
    rankings: list[list[str]],
    k: int = 60,
    top_n: int | None = None,
) -> list[str]:
    """Reciprocal Rank Fusion — 여러 랭킹 리스트를 순위만 사용해 융합.

    score(d) = Σ_i 1 / (k + rank_i(d))

    k=60 은 RRF 원논문(Cormack et al. 2009) 표준값. 분포 범위가 크게 다른
    스코어들 (cosine 0~1 vs BM25 0~수십) 을 동일 척도로 묶어 주는 효과.

    Args:
        rankings: 각 retriever 의 순위 리스트 (chunk_id 만, 순서가 곧 rank).
        k: RRF 상수 (보통 60).
        top_n: 결과 상한 (None 이면 모든 융합 결과 반환).

    Returns:
        융합 점수 내림차순 chunk_id 리스트.
    """
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + rank + 1)
    ordered = [cid for cid, _ in sorted(fused.items(), key=lambda x: x[1], reverse=True)]
    return ordered if top_n is None else ordered[:top_n]
