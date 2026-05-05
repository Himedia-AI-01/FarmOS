"""Redis Stack 8.4+ backend for the subsidy RAG.

Schema (FT.CREATE on HASH key prefix ``sub:gov:``):
    embedding   VECTOR HNSW DIM 4096 DISTANCE_METRIC COSINE     ← Solar dense
    text        TEXT                                            ← raw content + ctx prefix
    text_ko     TEXT                                            ← Kiwi-tokenised (Korean BM25)
    parent_id   TAG                                             ← "CH1_S011" (dedup-by-parent)
    clause_id   TAG                                             ← "II-8" (clause-direct lookup)
    sub_path    TAG                                             ← "가.①" (debug + ordering)
    chapter     TAG                                             ← "CHAPTER 1" (filter)
    section_type TAG                                            ← "본문" | "별표"
    page_start  NUMERIC SORTABLE
    page_end    NUMERIC
    leaf_idx    NUMERIC SORTABLE                                ← stable order within parent

Retrieval:
    1. ``hybrid_search(query)``   — dense + BM25 fused via ``FT.HYBRID``
       (RRF default, LINEAR optional via ``SUBSIDY_HYBRID_FUSION``).
    2. ``get_clause_leaves(id)``  — TAG filter ``@clause_id:{II-8}`` SORTBY leaf_idx.
       Replaces the old 13K-char joined string from get_clauses_index().
    3. ``get_by_parent(pid)``     — fetch siblings of a leaf (for "show context").

Why FT.HYBRID:
    Single round-trip server-side fusion. Eliminates Python-side RRF + the
    twin FT.SEARCH calls. Saves 30-50ms per query and removes a class of
    score-normalisation bugs.

Failure mode:
    Index/search errors raise — gov_rag dispatches to the chroma backend if
    SUBSIDY_RAG_BACKEND=chroma or if Redis backend init throws.
"""

from __future__ import annotations

import asyncio
import logging
import re
import struct
import time
from typing import TYPE_CHECKING, Any

from redis.commands.search.field import (
    NumericField,
    TagField,
    TextField,
    VectorField,
)
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.exceptions import ResponseError

from app.core.config import settings
from app.core.redis import (
    get_capabilities,
    get_redis,
    get_redis_bytes,
    require_redis,
)
from app.services.subsidy.embedding_cache import CachedSolarEmbeddings
from app.services.subsidy.bm25_index import _kiwi_tokenize  # reuse Kiwi morpheme tokeniser

if TYPE_CHECKING:
    from app.services.subsidy.chunker import LeafChunk

logger = logging.getLogger(__name__)


VECTOR_DIM = 4096   # Solar large
INDEX_NAME = settings.REDIS_SUBSIDY_INDEX_NAME            # "idx:gov_subsidy_v2"
KEY_PREFIX = settings.REDIS_SUBSIDY_PREFIX                # "sub:gov:"

# HNSW knobs — tuned for our scale (~280 leaves):
#   M=16, EF_CONSTRUCTION=200, EF_RUNTIME=200 → ~95% recall@10 with ms-level latency.
#   At this size the HNSW config is overkill; FLAT would also work in <5ms.
#   We use HNSW so the same code path scales when (eventually) we index reviews.
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 200
HNSW_EF_RUNTIME = 200


# ── FT.HYBRID auto-detect ──────────────────────────────────────────────────
#
# Tri-state runtime flag for the FT.HYBRID code path:
#   None  → not yet probed; try once and learn from the outcome.
#   True  → server accepts our command shape; use it from now on.
#   False → server rejected with a syntax/arg error; fall back to two-call
#           permanently (until process restart).
#
# This avoids hardcoding USE_FT_HYBRID = False (which throws away a 30-50ms
# saving on every query forever) while still being safe — the very first
# query that hits an incompatible server flips the flag and uses the
# two-call path, with the original error logged once for diagnostics.
#
# Reset on process restart, since Redis upgrades / Search module updates can
# change the syntax acceptance.
_ft_hybrid_works: bool | None = None
# Patterns that indicate "the server does not accept this command shape" —
# any of these in the error string flips us off without retrying. Other
# errors (network, OOM, missing index) are NOT cause to permanently disable.
_FT_HYBRID_SYNTAX_HINTS: tuple[str, ...] = (
    "syntax error",
    "wrong number of arguments",
    "unknown argument",
    "unknown command",
    "ft.hybrid",
)


def _is_ft_hybrid_syntax_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(hint in msg for hint in _FT_HYBRID_SYNTAX_HINTS)


# ── Schema management ────────────────────────────────────────────────────


def _hash_key(leaf_id: str) -> str:
    return f"{KEY_PREFIX}{leaf_id}"


async def ensure_index(drop_existing: bool = False) -> bool:
    """Create the FT index if missing. Returns True if created (or already present).

    Args:
        drop_existing: if True, FT.DROPINDEX first. Used during re-ingest with
            schema changes. Documents are NOT deleted (DD flag omitted) so the
            indexer can re-attach to the same HASH keys.
    """
    rb = require_redis()  # text client; FT commands accept str args

    if drop_existing:
        try:
            await rb.execute_command("FT.DROPINDEX", INDEX_NAME)
            logger.info("ft.dropindex %s", INDEX_NAME)
        except ResponseError as e:
            msg = str(e).lower()
            # "index not found" / "unknown index" / "search_index_not_found" are
            # the expected outcomes on a fresh DB — don't warn.
            if any(s in msg for s in ("not found", "unknown index", "no such index")):
                pass
            else:
                logger.warning("ft.dropindex.failed name=%s err=%s", INDEX_NAME, e)

    # Check existing — FT.INFO succeeds iff the index is registered.
    try:
        await rb.execute_command("FT.INFO", INDEX_NAME)
        logger.info("ft.index_exists name=%s", INDEX_NAME)
        return True
    except ResponseError:
        pass  # Not created yet → create below.

    schema = (
        VectorField(
            "embedding",
            algorithm="HNSW",
            attributes={
                "TYPE": "FLOAT32",
                "DIM": VECTOR_DIM,
                "DISTANCE_METRIC": "COSINE",
                "M": HNSW_M,
                "EF_CONSTRUCTION": HNSW_EF_CONSTRUCTION,
            },
        ),
        TextField("text"),
        TextField("text_ko"),
        TagField("parent_id"),
        TagField("clause_id"),
        TagField("sub_path"),
        TagField("chapter"),
        TagField("section_type"),
        NumericField("page_start", sortable=True),
        NumericField("page_end"),
        NumericField("leaf_idx", sortable=True),
    )
    definition = IndexDefinition(prefix=[KEY_PREFIX], index_type=IndexType.HASH)

    try:
        await rb.ft(INDEX_NAME).create_index(fields=schema, definition=definition)
        logger.info("ft.create_index name=%s prefix=%s", INDEX_NAME, KEY_PREFIX)
        return True
    except ResponseError as e:
        if "Index already exists" in str(e):
            return True
        raise


async def drop_documents() -> int:
    """Delete all leaf hashes under KEY_PREFIX. Returns count of keys removed.

    Used by ``reset()`` for a clean re-ingest. Index definition stays so the
    next ingest can write to the same FT index.
    """
    rb = require_redis()
    cursor: Any = 0
    deleted = 0
    while True:
        cursor, keys = await rb.scan(cursor=cursor, match=f"{KEY_PREFIX}*", count=500)
        if keys:
            await rb.delete(*keys)
            deleted += len(keys)
        # Normalise bytes cursor (rb may be a bytes-mode client) — comparing
        # `b"0" == 0` is always False and would loop forever.
        if isinstance(cursor, (bytes, bytearray)):
            try:
                cursor = int(cursor)
            except ValueError:
                break
        if cursor == 0:
            break
    if deleted:
        logger.info("redis_index.deleted_documents n=%d prefix=%s", deleted, KEY_PREFIX)
    return deleted


async def doc_count() -> int:
    """Number of indexed leaves. Reads FT.INFO 'num_docs' field."""
    rb = require_redis()
    try:
        info = await rb.execute_command("FT.INFO", INDEX_NAME)
    except ResponseError:
        return 0
    # FT.INFO returns flat [k, v, k, v, ...] — convert to dict.
    info_dict = dict(zip(info[::2], info[1::2], strict=False))
    return int(info_dict.get("num_docs", 0))


# ── Indexing ─────────────────────────────────────────────────────────────


def _vec_to_bytes(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


async def index_leaves(
    leaves: list["LeafChunk"],
    contextual_prefixes: dict[str, str],
    embeddings: list[list[float]],
) -> int:
    """Bulk-index leaves into Redis. Caller pre-computes embeddings + prefixes.

    Layout per leaf (HASH at key ``sub:gov:{leaf.id}``):
        embedding  bytes(float32 × 4096)
        text       contextual_prefix + "\n\n" + leaf.content
        text_ko    Kiwi morpheme tokens of the same text (BM25 substrate)
        parent_id  leaf.parent_id
        clause_id  leaf.clause_id
        sub_path   leaf.sub_path
        chapter    leaf.chapter
        section_type leaf.section_type
        page_start leaf.page_start
        page_end   leaf.page_end
        leaf_idx   leaf.leaf_idx

    Pipelined writes — one round-trip per pipeline batch, not per leaf.
    """
    if not leaves:
        return 0
    if len(leaves) != len(embeddings):
        raise ValueError(
            f"index_leaves: leaves={len(leaves)} embeddings={len(embeddings)} mismatch"
        )

    rb = require_redis()
    rb_bytes = get_redis_bytes()
    if rb_bytes is None:
        raise RuntimeError("Redis bytes client unavailable — should not happen if require_redis() succeeded")

    BATCH = 50
    total = 0
    for start in range(0, len(leaves), BATCH):
        batch_leaves = leaves[start : start + BATCH]
        batch_vecs = embeddings[start : start + BATCH]
        # Use the bytes client for HSET — embedding field is binary float32.
        # text/text_ko fields are still bytes-encodable strings; redis-py
        # encodes both transparently on a non-decoding client.
        pipe = rb_bytes.pipeline(transaction=False)
        for leaf, vec in zip(batch_leaves, batch_vecs, strict=True):
            ctx = contextual_prefixes.get(leaf.id, "") or ""
            text_for_search = (
                f"{ctx}\n\n{leaf.content}" if ctx else leaf.content
            )
            tokens = " ".join(_kiwi_tokenize(text_for_search))
            # IMPORTANT: field names MUST be plain str to match the FT schema
            # (FT.CREATE registered "embedding", "text" etc. as strings).
            # If we pass bytes keys, redis-py stores them as bytes-typed RESP
            # bulks and the FT index won't match. Values can be bytes (binary
            # vectors) or str (everything else).
            mapping: dict[str, Any] = {
                "embedding": _vec_to_bytes(vec),
                "text": text_for_search,
                "text_ko": tokens,
                "parent_id": leaf.parent_id or "",
                "clause_id": leaf.clause_id or "",
                "sub_path": leaf.sub_path or "",
                "chapter": leaf.chapter or "",
                "section_type": leaf.section_type or "",
                "page_start": leaf.page_start,
                "page_end": leaf.page_end,
                "leaf_idx": leaf.leaf_idx,
            }
            pipe.hset(_hash_key(leaf.id), mapping=mapping)
        await pipe.execute()
        total += len(batch_leaves)
    logger.info("redis_index.indexed n=%d prefix=%s", total, KEY_PREFIX)
    return total


# ── Search ──────────────────────────────────────────────────────────────


def _format_text_ko_query(query: str) -> str:
    """Kiwi-tokenise + escape for FT TEXT field.

    FT query language treats ``- ( ) | "`` as operators. Korean tokens shouldn't
    contain those, but defensively we drop empty tokens and quote each one to
    force phrase-OR matching.

    Result: ``(token1 | token2 | token3)`` — OR-of-tokens, BM25 ranks by overlap.
    """
    tokens = [t for t in _kiwi_tokenize(query) if t]
    if not tokens:
        return "*"
    # FT escape: "|" is fine inside | groups; we rely on the tokenizer producing safe strings.
    return "(" + " | ".join(tokens) + ")"


async def hybrid_search(
    query: str,
    *,
    k: int = 10,
    ef_runtime: int = HNSW_EF_RUNTIME,
    chapter: str | None = None,
    section_type: str | None = None,
) -> list[dict[str, Any]]:
    """Hybrid (dense + BM25) search via FT.HYBRID with server-side score fusion.

    Returns list of dicts: ``{id, score, parent_id, clause_id, sub_path, text,
    page_start, page_end, leaf_idx}`` ordered by fused score descending.

    Args:
        query: user query — will be Kiwi-tokenised for BM25 substrate.
        k: number of results to return (top-k after fusion).
        ef_runtime: HNSW search width — higher = more accurate, slower.
        chapter, section_type: optional TAG filters (e.g. exclude 별표).

    Fusion mode controlled by settings.SUBSIDY_HYBRID_FUSION:
        rrf    : Reciprocal Rank Fusion (k=60). Default. No score-norm issues.
        linear : LINEAR ALPHA=settings.SUBSIDY_HYBRID_ALPHA, BETA=1-ALPHA.
                 ALPHA biases dense (Solar). 0.7 → 70% dense / 30% BM25.
    """
    caps = get_capabilities()
    # FT.HYBRID command syntax has been in flux across Redis 8.x point
    # releases. We auto-detect on first use (see _ft_hybrid_works above):
    # try the command, fall back to two-call permanently if the server
    # rejects with a syntax-class error. This way we get the 30-50ms
    # saving on supported servers without hand-flipping a flag.
    global _ft_hybrid_works
    if caps is None or not caps.supports_ft_hybrid or _ft_hybrid_works is False:
        return await _hybrid_search_two_call(
            query=query, k=k, ef_runtime=ef_runtime,
            chapter=chapter, section_type=section_type,
        )

    rb = require_redis()
    rb_bytes = get_redis_bytes()
    if rb_bytes is None:
        raise RuntimeError("Redis bytes client unavailable")

    # 1) Solar query embed (cache-backed)
    embedder = CachedSolarEmbeddings()
    qvec = await embedder.aembed_query(query)
    qvec_bytes = _vec_to_bytes(qvec)

    # 2) Build the FT.HYBRID command. Filters compose with the SEARCH clause
    #    via @field:{tag} predicates (also applied to the VSIM filter).
    text_q = _format_text_ko_query(query)
    filter_parts: list[str] = []
    if chapter:
        filter_parts.append(f"@chapter:{{{_escape_tag(chapter)}}}")
    if section_type:
        filter_parts.append(f"@section_type:{{{_escape_tag(section_type)}}}")
    filter_expr = " ".join(filter_parts)

    search_clause = f"@text_ko:{text_q}"
    if filter_expr:
        search_clause = f"{search_clause} {filter_expr}"

    vsim_filter = filter_expr or "*"

    # FT.HYBRID parts (Redis 8.4 syntax):
    #   FT.HYBRID <idx>
    #     SEARCH "<text>" SCORER 4 BM25 1.2 0.75 YIELD_SCORE_AS bm25_s
    #     VSIM @embedding $qv FILTER "<filter>" KNN 4 K <2k> EF_RUNTIME <er> YIELD_SCORE_AS vec_s
    #     COMBINE RRF 2 K 60         (or LINEAR 4 ALPHA 0.7 BETA 0.3)
    #     RETURN 9 parent_id clause_id sub_path text page_start page_end leaf_idx __score __key
    #     LIMIT 0 K
    #     PARAMS 2 qv <bytes>
    #     DIALECT 2
    fusion = settings.SUBSIDY_HYBRID_FUSION.lower()
    if fusion == "linear":
        alpha = float(settings.SUBSIDY_HYBRID_ALPHA)
        beta = max(0.0, 1.0 - alpha)
        combine_args = ("COMBINE", "LINEAR", "4", "ALPHA", str(alpha), "BETA", str(beta))
    else:
        combine_args = ("COMBINE", "RRF", "2", "K", "60")

    pool = max(2 * k, 20)  # Pull more candidates than k, then COMBINE narrows.

    cmd: list[Any] = [
        "FT.HYBRID", INDEX_NAME,
        "SEARCH", search_clause,
        "SCORER", "BM25", "1.2", "0.75",
        "YIELD_SCORE_AS", "bm25_s",
        "VSIM", "@embedding", "$qv",
        "FILTER", vsim_filter,
        "KNN", str(pool), "EF_RUNTIME", str(ef_runtime),
        "YIELD_SCORE_AS", "vec_s",
        *combine_args,
        "RETURN", "9",
        "parent_id", "clause_id", "sub_path", "text",
        "page_start", "page_end", "leaf_idx", "__score", "__key",
        "LIMIT", "0", str(k),
        "PARAMS", "2", "qv", qvec_bytes,
        "DIALECT", "2",
    ]

    t0 = time.perf_counter()
    try:
        raw = await rb_bytes.execute_command(*cmd)
    except ResponseError as e:
        if _is_ft_hybrid_syntax_error(e):
            # Server doesn't speak our FT.HYBRID dialect — disable for the
            # rest of the process and emit a one-time diagnostic. Falls back
            # to the two-call path which consistently works on Search 8.x.
            _ft_hybrid_works = False
            logger.warning(
                "ft.hybrid.unsupported_syntax err=%s — disabling for this process; "
                "two-call RRF will be used instead (~30-50ms slower per query)",
                e,
            )
        else:
            # Transient error (network, OOM, missing index) — keep the flag
            # tri-state so a one-off failure doesn't permanently disable.
            logger.warning("ft.hybrid.failed err=%s — fallback to two-call", e)
        return await _hybrid_search_two_call(
            query=query, k=k, ef_runtime=ef_runtime,
            chapter=chapter, section_type=section_type,
        )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # First successful call latches the flag → subsequent queries skip the
    # `or _ft_hybrid_works is False` check immediately.
    if _ft_hybrid_works is None:
        _ft_hybrid_works = True
        logger.info("ft.hybrid.detected_supported — using server-side fusion")

    results = _parse_search_response(raw)
    logger.info(
        "redis_index.hybrid_search n=%d k=%d fusion=%s ms=%.0f q=%r",
        len(results), k, fusion, elapsed_ms, query[:60],
    )
    return results


async def _hybrid_search_two_call(
    *,
    query: str,
    k: int,
    ef_runtime: int,
    chapter: str | None,
    section_type: str | None,
) -> list[dict[str, Any]]:
    """Fallback for Search < 8.4 — two FT.SEARCH calls + Python RRF.

    Mirrors the legacy chroma path's behaviour but on top of Redis.
    """
    rb = require_redis()
    rb_bytes = get_redis_bytes()
    if rb_bytes is None:
        raise RuntimeError("Redis bytes client unavailable")

    embedder = CachedSolarEmbeddings()
    qvec = await embedder.aembed_query(query)
    qvec_bytes = _vec_to_bytes(qvec)

    pool = max(2 * k, 20)
    text_q = _format_text_ko_query(query)
    filter_parts: list[str] = []
    if chapter:
        filter_parts.append(f"@chapter:{{{_escape_tag(chapter)}}}")
    if section_type:
        filter_parts.append(f"@section_type:{{{_escape_tag(section_type)}}}")
    filter_expr = " ".join(filter_parts)

    # AS alias is required to reference the KNN distance in SORTBY/RETURN
    # under DIALECT 2. Order matters in this clause: parameters first, then AS.
    knn_q = (
        f"({filter_expr})=>[KNN {pool} @embedding $qv EF_RUNTIME {ef_runtime}]=>{{$yield_distance_as: vec_score}}"
        if filter_expr else f"*=>[KNN {pool} @embedding $qv EF_RUNTIME {ef_runtime}]=>{{$yield_distance_as: vec_score}}"
    )

    dense_cmd = [
        "FT.SEARCH", INDEX_NAME, knn_q,
        "RETURN", "9", "parent_id", "clause_id", "sub_path",
        "text", "page_start", "page_end", "leaf_idx", "__key", "vec_score",
        "SORTBY", "vec_score",
        "PARAMS", "2", "qv", qvec_bytes,
        "DIALECT", "2",
        "LIMIT", "0", str(pool),
    ]

    text_clause = f"@text_ko:{text_q}"
    if filter_expr:
        text_clause = f"{text_clause} {filter_expr}"
    sparse_cmd = [
        "FT.SEARCH", INDEX_NAME, text_clause,
        "SCORER", "BM25",
        "RETURN", "8", "parent_id", "clause_id", "sub_path",
        "text", "page_start", "page_end", "leaf_idx", "__key",
        "LIMIT", "0", str(pool),
    ]

    dense_raw, sparse_raw = await asyncio.gather(
        rb_bytes.execute_command(*dense_cmd),
        rb.execute_command(*sparse_cmd),
    )
    dense_hits = _parse_search_response(dense_raw)
    sparse_hits = _parse_search_response(sparse_raw)

    # RRF k=60
    fused: dict[str, dict[str, Any]] = {}
    for rank, h in enumerate(dense_hits, start=1):
        fused.setdefault(h["id"], h)["_rrf"] = 1.0 / (60 + rank)
    for rank, h in enumerate(sparse_hits, start=1):
        existing = fused.get(h["id"])
        if existing:
            existing["_rrf"] += 1.0 / (60 + rank)
        else:
            h["_rrf"] = 1.0 / (60 + rank)
            fused[h["id"]] = h

    out = sorted(fused.values(), key=lambda x: x["_rrf"], reverse=True)
    for r in out:
        r["score"] = r.pop("_rrf")
    return out[:k]


def _parse_search_response(raw: list[Any]) -> list[dict[str, Any]]:
    """Normalise FT.SEARCH / FT.HYBRID response.

    Both commands return ``[count, key1, [field1, val1, ...], key2, ...]``.
    We extract the docs into dicts with consistent keys.
    """
    if not raw:
        return []
    # First element is count of results
    out: list[dict[str, Any]] = []
    i = 1
    while i < len(raw):
        key = raw[i]
        i += 1
        # FT.HYBRID and FT.SEARCH may return scores inline before fields.
        # Modern dialect 2 returns: key, [field, val, field, val, ...]
        if i >= len(raw):
            break
        fields = raw[i]
        i += 1
        if not isinstance(fields, (list, tuple)):
            continue
        d: dict[str, Any] = {"id": _str(key).removeprefix(KEY_PREFIX), "key": _str(key)}
        for j in range(0, len(fields) - 1, 2):
            fk = _str(fields[j])
            fv = fields[j + 1]
            if fk in ("page_start", "page_end", "leaf_idx"):
                try:
                    d[fk] = int(_str(fv))
                except (ValueError, TypeError):
                    d[fk] = 0
            elif fk in ("__score", "score"):
                try:
                    d["score"] = float(_str(fv))
                except (ValueError, TypeError):
                    d["score"] = 0.0
            elif fk == "__key":
                # already captured as 'key'
                pass
            else:
                d[fk] = _str(fv) if isinstance(fv, (bytes, str)) else fv
        if "score" not in d:
            d["score"] = 0.0
        out.append(d)
    return out


def _str(x: Any) -> str:
    if isinstance(x, bytes):
        try:
            return x.decode("utf-8")
        except UnicodeDecodeError:
            return ""
    return str(x) if x is not None else ""


def _escape_tag(s: str) -> str:
    """Escape FT TAG operators inside the value.

    TAG queries treat these as syntax: ``,.<>{}[]"':;!@#$%^&*()-+=~|``
    Subsidy clause ids use ``-`` (e.g. "II-3", "C2-VI"), so dashes must be
    escaped. Otherwise the parser reads ``-`` as a negation operator and
    splits the value into separate tokens.
    """
    out = s
    # Strip control characters (NUL, newlines, tabs, etc.) up front — these
    # would either be silently swallowed by the FT parser or, worse, smuggle
    # a `}` past the {VALUE} closer if the planner ever surfaces them.
    out = "".join(ch for ch in out if ord(ch) >= 0x20)
    # Escape backslash itself first to avoid double-escaping below.
    out = out.replace("\\", "\\\\")
    for ch in [" ", ",", ".", "<", ">", "{", "}", "[", "]", '"', "'",
               ":", ";", "!", "@", "#", "$", "%", "^", "&", "*",
               "(", ")", "-", "+", "=", "~", "|", "/"]:
        out = out.replace(ch, f"\\{ch}")
    return out


# Whitelist for clause-direct lookups (II-3, C2-VI, BP4 …). Anything outside
# this shape is rejected — the planner should never produce a clause id with
# spaces, control chars, or punctuation beyond ``-``.
_CLAUSE_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,32}$")


# ── Clause-direct lookup (replaces get_clauses_index) ────────────────────


async def get_clause_leaves(
    clause_id: str, *, max_chars: int = 3_000,
) -> list[dict[str, Any]]:
    """Fetch leaves for a clause id ordered by leaf_idx, capped at max_chars total.

    Replaces gov_rag.get_clauses_index() which returned a single 13K-char
    joined string per clause. The cap (default 3000 chars ≈ 750 tokens) keeps
    the LLM input small for clause-direct queries like "II-3 알려줘".

    Args:
        clause_id: e.g. "II-3", "C2-VI", "BP4". Empty string → empty list.
        max_chars: total content budget across leaves; the iteration stops
                   once adding a leaf would exceed.

    Returns:
        list of dicts (same shape as hybrid_search results).
    """
    if not clause_id:
        return []
    if not _CLAUSE_ID_RE.match(clause_id):
        logger.warning("redis_index.invalid_clause_id value=%r — rejected", clause_id[:64])
        return []
    rb = require_redis()
    cmd = [
        "FT.SEARCH", INDEX_NAME,
        f"@clause_id:{{{_escape_tag(clause_id)}}}",
        "RETURN", "7",
        "parent_id", "clause_id", "sub_path",
        "text", "page_start", "page_end", "leaf_idx",
        "SORTBY", "leaf_idx", "ASC",
        "LIMIT", "0", "100",
    ]
    raw = await rb.execute_command(*cmd)
    leaves = _parse_search_response(raw)
    out: list[dict[str, Any]] = []
    total = 0
    for leaf in leaves:
        body = leaf.get("text", "") or ""
        if total + len(body) > max_chars and out:
            break
        out.append(leaf)
        total += len(body)
    return out


async def get_by_parent(parent_id: str) -> list[dict[str, Any]]:
    """Return all leaves with the given parent_id ordered by leaf_idx.

    Used for "show context" — agent has a leaf and wants its siblings without
    re-searching.
    """
    if not parent_id:
        return []
    rb = require_redis()
    cmd = [
        "FT.SEARCH", INDEX_NAME,
        f"@parent_id:{{{_escape_tag(parent_id)}}}",
        "RETURN", "7",
        "parent_id", "clause_id", "sub_path",
        "text", "page_start", "page_end", "leaf_idx",
        "SORTBY", "leaf_idx", "ASC",
        "LIMIT", "0", "50",
    ]
    raw = await rb.execute_command(*cmd)
    return _parse_search_response(raw)
