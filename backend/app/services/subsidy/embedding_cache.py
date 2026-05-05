"""Solar embedding wrapper with Redis cache.

Why a deterministic cache:
    Solar embeddings are stable for a given (model, text) pair. Re-indexing the
    same PDF, repeating an evaluation, or hitting Redis after a restart should
    not re-pay the ~300ms HTTP roundtrip per query/passage.

Cache key:
    sha1("{model}|{normalized_text}") — model in the key so a future swap to
    a different Solar model doesn't return stale vectors.

Storage format:
    HASH at key ``emb:{sha1}`` with fields:
      v       float32 little-endian bytes (4096-dim → 16,384 bytes)
      m       model name (debug aid)
      n       text length (debug aid)
    Storing as a HASH (not raw STRING bytes) lets us add metadata without
    breaking the binary value. The bytes client (decode_responses=False)
    HGETs the ``v`` field and converts back to list[float].

TTL:
    REDIS_EMBEDDING_TTL_DAYS controls. Default 30 days — long because the data
    is deterministic; we never want to re-pay if we don't have to.

Failure mode:
    Any Redis exception → log + fall through to live Solar call. Caller never
    sees a cache failure as a hard error.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import struct
import threading
from typing import Awaitable, Iterable, Sequence, TypeVar

from langchain_upstage import UpstageEmbeddings

from app.core.config import settings
from app.core.redis import get_redis_bytes

logger = logging.getLogger(__name__)


# Solar large = 4096 dim. We assert at runtime to catch a model swap.
EXPECTED_DIM = 4096
_FIELD_VEC = b"v"
_FIELD_MODEL = b"m"
_FIELD_NLEN = b"n"


def _cache_key(model: str, text: str) -> str:
    # Normalise whitespace — different parsers (chunker pre/post split) sometimes
    # emit trailing newlines that are semantically identical.
    normalized = " ".join(text.split())
    blob = f"{model}|{normalized}".encode("utf-8")
    return f"emb:{hashlib.sha1(blob).hexdigest()}"


def _vec_to_bytes(vec: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _bytes_to_vec(b: bytes) -> list[float]:
    n = len(b) // 4
    return list(struct.unpack(f"<{n}f", b))


_T = TypeVar("_T")


# Sync-API bridge. LangChain's Embeddings contract is sync, but we need to
# touch the async Redis client. asyncio.run() is unsafe here because:
#   1) FastAPI may invoke embed_documents/embed_query indirectly from a coroutine
#      (e.g., agentic ingest) — asyncio.run() raises RuntimeError on a running loop.
#   2) get_redis_bytes() returns a pool-bound client created on the FastAPI loop;
#      running a fresh loop attaches the awaitable to a different loop and the
#      Redis client raises "Future attached to a different loop".
#
# Strategy: if we're already on a running loop, dispatch to a dedicated background
# thread that owns its own loop and Redis is invoked from within that thread's
# loop. Otherwise (true sync caller, e.g. CLI/eval scripts), use asyncio.run().
_bg_loop: asyncio.AbstractEventLoop | None = None
_bg_loop_lock = threading.Lock()


def _get_bg_loop() -> asyncio.AbstractEventLoop:
    global _bg_loop
    with _bg_loop_lock:
        if _bg_loop is not None and not _bg_loop.is_closed():
            return _bg_loop
        loop = asyncio.new_event_loop()
        t = threading.Thread(
            target=loop.run_forever,
            name="emb-cache-bg-loop",
            daemon=True,
        )
        t.start()
        _bg_loop = loop
        return loop


def _run_sync(coro: Awaitable[_T]) -> _T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — safe to run synchronously in this thread.
        return asyncio.run(coro)  # type: ignore[arg-type]
    # Running loop in this thread — bounce to background loop thread.
    fut = asyncio.run_coroutine_threadsafe(coro, _get_bg_loop())  # type: ignore[arg-type]
    return fut.result()


class CachedSolarEmbeddings:
    """Drop-in for ``UpstageEmbeddings`` with Redis backed read-through cache.

    Sync-only API to match LangChain's ``Embeddings`` contract used by current
    ingest code. Internally uses ``asyncio.run`` for one-shot Redis trips —
    acceptable because callers are already in thread-pool contexts (LangChain
    embed_documents is sync).

    For pure async hot paths (search query embedding), use ``aembed_query``.
    """

    def __init__(self, model: str | None = None) -> None:
        self.model = model or "solar-embedding-1-large"
        self._raw = UpstageEmbeddings(
            api_key=settings.UPSTAGE_API_KEY,
            model=self.model,
        )
        self._ttl_sec = settings.REDIS_EMBEDDING_TTL_DAYS * 86400

    # ── Sync API (used by indexer, eval, LangChain compatibility) ────────

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batch embed with cache. Returns vectors in the same order as input."""
        if not texts:
            return []
        return _run_sync(self._embed_batch(texts))

    def embed_query(self, text: str) -> list[float]:
        return _run_sync(self._embed_one(text))

    # ── Async API (preferred for hot search path) ────────────────────────

    async def aembed_query(self, text: str) -> list[float]:
        return await self._embed_one(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed_batch(texts)

    # ── Internals ────────────────────────────────────────────────────────

    async def _embed_one(self, text: str) -> list[float]:
        cached = await self._cache_get_one(text)
        if cached is not None:
            return cached
        # Fall through to live call. Solar SDK is sync — run in thread to avoid
        # blocking the event loop when called from FastAPI.
        vec = await asyncio.to_thread(self._raw.embed_query, text)
        self._validate_dim(vec)
        await self._cache_set_one(text, vec)
        return vec

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        # 1) Bulk lookup all keys with MGET-style pipeline
        cached_map = await self._cache_get_many(texts)
        # 2) Identify misses, preserve their input order
        misses_idx = [i for i, t in enumerate(texts) if cached_map.get(_cache_key(self.model, t)) is None]
        misses_text = [texts[i] for i in misses_idx]

        if misses_text:
            # Solar batches up to embed_documents max — let the SDK handle paging.
            new_vecs = await asyncio.to_thread(self._raw.embed_documents, misses_text)
            for v in new_vecs:
                self._validate_dim(v)
            # Persist in parallel; failures are logged but non-fatal.
            await self._cache_set_many(misses_text, new_vecs)
            for idx, v in zip(misses_idx, new_vecs, strict=True):
                cached_map[_cache_key(self.model, texts[idx])] = v

        return [cached_map[_cache_key(self.model, t)] for t in texts]

    async def _cache_get_one(self, text: str) -> list[float] | None:
        rb = get_redis_bytes()
        if rb is None:
            return None
        key = _cache_key(self.model, text)
        try:
            raw = await rb.hget(key, _FIELD_VEC)  # type: ignore[arg-type]
        except Exception as e:  # noqa: BLE001 — cache failures must never break callers
            logger.warning("emb_cache.get_failed key=%s err=%s", key, e)
            return None
        if not raw:
            return None
        try:
            v = _bytes_to_vec(raw)
        except struct.error as e:
            logger.warning("emb_cache.decode_failed key=%s err=%s — purging", key, e)
            try:
                await rb.delete(key)
            except Exception:  # noqa: BLE001
                pass
            return None
        if len(v) != EXPECTED_DIM:
            logger.warning("emb_cache.dim_mismatch key=%s got=%d expected=%d — purging",
                           key, len(v), EXPECTED_DIM)
            try:
                await rb.delete(key)
            except Exception:  # noqa: BLE001
                pass
            return None
        return v

    async def _cache_get_many(self, texts: Iterable[str]) -> dict[str, list[float] | None]:
        rb = get_redis_bytes()
        keys = [_cache_key(self.model, t) for t in texts]
        out: dict[str, list[float] | None] = {k: None for k in keys}
        if rb is None or not keys:
            return out
        try:
            # One pipeline per batch — N round trips collapsed into 1.
            pipe = rb.pipeline(transaction=False)
            for k in keys:
                pipe.hget(k, _FIELD_VEC)
            results = await pipe.execute()
        except Exception as e:  # noqa: BLE001
            logger.warning("emb_cache.bulk_get_failed n=%d err=%s", len(keys), e)
            return out
        for k, raw in zip(keys, results, strict=True):
            if not raw:
                continue
            try:
                v = _bytes_to_vec(raw)
            except struct.error:
                continue
            if len(v) == EXPECTED_DIM:
                out[k] = v
        return out

    async def _cache_set_one(self, text: str, vec: Sequence[float]) -> None:
        rb = get_redis_bytes()
        if rb is None:
            return
        key = _cache_key(self.model, text)
        try:
            pipe = rb.pipeline(transaction=False)
            pipe.hset(
                key,
                mapping={
                    _FIELD_VEC: _vec_to_bytes(vec),
                    _FIELD_MODEL: self.model.encode(),
                    _FIELD_NLEN: str(len(text)).encode(),
                },
            )
            if self._ttl_sec > 0:
                pipe.expire(key, self._ttl_sec)
            await pipe.execute()
        except Exception as e:  # noqa: BLE001
            logger.warning("emb_cache.set_failed key=%s err=%s", key, e)

    async def _cache_set_many(
        self, texts: Sequence[str], vecs: Sequence[Sequence[float]],
    ) -> None:
        rb = get_redis_bytes()
        if rb is None or not texts:
            return
        try:
            pipe = rb.pipeline(transaction=False)
            for t, v in zip(texts, vecs, strict=True):
                k = _cache_key(self.model, t)
                pipe.hset(
                    k,
                    mapping={
                        _FIELD_VEC: _vec_to_bytes(v),
                        _FIELD_MODEL: self.model.encode(),
                        _FIELD_NLEN: str(len(t)).encode(),
                    },
                )
                if self._ttl_sec > 0:
                    pipe.expire(k, self._ttl_sec)
            await pipe.execute()
        except Exception as e:  # noqa: BLE001
            logger.warning("emb_cache.bulk_set_failed n=%d err=%s", len(texts), e)

    def _validate_dim(self, v: Sequence[float]) -> None:
        if len(v) != EXPECTED_DIM:
            raise RuntimeError(
                f"Solar returned dim={len(v)} but expected {EXPECTED_DIM}. "
                f"Did the model change? Update EXPECTED_DIM and the FT schema together."
            )
