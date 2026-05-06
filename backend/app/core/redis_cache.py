"""Universal Redis-backed cache decorator for async functions.

용도:
    외부 API 호출 (KMA, KAMIS, NCPMS), 비싼 DB 조회, 일회성 LLM 응답 등을
    같은 입력으로 반복 호출할 때 Redis 에 결과를 캐싱한다.

대상 (적용 후보):
    - app/core/weather_client.py:get_weather()           (KMA, TTL ~10분)
    - app/services/kamis.py:KamisService.get_*()         (KAMIS, TTL 1~6시간)
    - app/api/pesticide.py:search_pesticide()            (DB ILIKE, TTL ~1시간)
    - app/services/farm_agent answer (deterministic, TTL 1시간)

설계 원칙:
    - graceful fallback: Redis 가 비활성/장애여도 함수 동작에 영향 없음
    - JSON 직렬화: 결과는 항상 json.dumps 로 변환. dict/list/primitive 한정.
      Pydantic 모델이나 dataclass 가 필요하면 caller 가 .dict()/asdict() 후 호출.
    - keygen 인자: 기본은 함수 이름 + 모든 인자 sha1. caller 가 의미적 keygen 제공 가능.
    - 부정 결과(None/빈 dict/빈 list)는 캐시하지 않음 (transient error 인지 모르므로
      재시도 보호). negative_cache=True 옵션으로 명시적으로 활성화 가능.
    - 동일 process 내 in-flight dedupe (asyncio.Lock per key) — thundering herd 방지.

사용 예:
    from app.core.redis_cache import redis_cached

    @redis_cached(prefix="kma:weather", ttl=600)
    async def get_weather(...) -> dict:
        ...

    @redis_cached(prefix="kamis:daily", ttl=3600,
                  keygen=lambda **kw: f"{kw['item_code']}:{kw['kind_code']}")
    async def get_daily_prices(...): ...
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
from collections import OrderedDict
from typing import Any, Awaitable, Callable, TypeVar

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# Per-key in-flight locks. Prevents thundering herd: when 100 requests arrive
# at once for the same key with empty cache, only one upstream call fires.
#
# Bounded LRU: a high-cardinality keygen (e.g. user-supplied search query as
# part of the suffix) would otherwise grow the dict without bound for the
# lifetime of the process. Evict the oldest entries past the cap.
_INFLIGHT_LOCK_CAP = 4096
_inflight_locks: "OrderedDict[str, asyncio.Lock]" = OrderedDict()
_inflight_lock_creator = asyncio.Lock()  # protects _inflight_locks dict mutation

T = TypeVar("T")


def _default_keygen(*args: Any, **kwargs: Any) -> str:
    """Deterministic key from positional + keyword args.

    Uses sha1 — collision risk is negligible for cache-key purposes (~1 in 2^160).
    """
    blob = json.dumps([args, sorted(kwargs.items())], default=str, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


async def _get_inflight_lock(key: str) -> asyncio.Lock:
    """Get-or-create the per-key lock. Two-step under a creator lock to avoid
    racing on dict mutation. Maintains LRU eviction past the cap."""
    lock = _inflight_locks.get(key)
    if lock is not None:
        # Touch — move to MRU end without rebuilding the dict.
        _inflight_locks.move_to_end(key)
        return lock
    async with _inflight_lock_creator:
        lock = _inflight_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _inflight_locks[key] = lock
            # Evict the LRU entry while over cap. Locks held by an in-flight
            # request stay reachable via the caller's `async with lock:` — we
            # only drop our reference, which is safe.
            while len(_inflight_locks) > _INFLIGHT_LOCK_CAP:
                _inflight_locks.popitem(last=False)
        else:
            _inflight_locks.move_to_end(key)
        return lock


def _is_empty_result(value: Any) -> bool:
    """Heuristic: don't cache results that look like transient failures."""
    if value is None:
        return True
    if isinstance(value, (list, dict, str)) and len(value) == 0:
        return True
    return False


def redis_cached(
    *,
    prefix: str,
    ttl: int,
    keygen: Callable[..., str] | None = None,
    negative_cache: bool = False,
    bypass: Callable[..., bool] | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator factory — applies Redis read-through cache to an async function.

    Args:
        prefix: cache key namespace, e.g. "kma:weather". Final key is
            ``{prefix}:{keygen(*args, **kwargs)}``.
        ttl: seconds. EXPIRE applied on every successful set.
        keygen: optional custom key suffix function. Default sha1's all args.
        negative_cache: if True, empty/None results are cached (saves repeat
            failures). Default False — transient errors get retried.
        bypass: optional predicate that, when True, skips cache entirely
            (read+write both bypassed). E.g. dev mode flag.

    Returns:
        Decorator that wraps an async function.
    """
    keygen = keygen or _default_keygen

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            # 1. Bypass check (optional)
            if bypass is not None:
                try:
                    if bypass(*args, **kwargs):
                        return await fn(*args, **kwargs)
                except Exception:  # noqa: BLE001 — bypass failures shouldn't break cache
                    pass

            # 2. Compose key
            try:
                suffix = keygen(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                logger.warning("redis_cache.keygen_failed prefix=%s err=%s", prefix, e)
                return await fn(*args, **kwargs)
            key = f"{prefix}:{suffix}"

            client = get_redis()

            # 3. Cache lookup
            if client is not None:
                try:
                    raw = await client.get(key)
                except Exception as e:  # noqa: BLE001
                    logger.warning("redis_cache.get_failed key=%s err=%s", key, e)
                    raw = None
                if raw:
                    try:
                        return json.loads(raw)  # type: ignore[no-any-return]
                    except json.JSONDecodeError:
                        logger.warning("redis_cache.decode_failed key=%s — purging", key)
                        try:
                            await client.delete(key)
                        except Exception:  # noqa: BLE001
                            pass

            # 4. In-flight dedupe (per-key asyncio lock)
            lock = await _get_inflight_lock(key)
            async with lock:
                # Re-check cache after acquiring lock — another coroutine may
                # have populated it during the wait.
                if client is not None:
                    try:
                        raw = await client.get(key)
                    except Exception:  # noqa: BLE001
                        raw = None
                    if raw:
                        try:
                            return json.loads(raw)  # type: ignore[no-any-return]
                        except json.JSONDecodeError:
                            pass

                # 5. Live call
                result = await fn(*args, **kwargs)

                # 6. Write cache (skip empty unless explicit)
                if client is not None and (negative_cache or not _is_empty_result(result)):
                    try:
                        payload = json.dumps(result, ensure_ascii=False, default=str)
                        await client.setex(key, ttl, payload)
                    except (TypeError, ValueError) as e:
                        # Result not JSON-serializable — don't crash, just skip cache.
                        logger.warning(
                            "redis_cache.serialize_failed key=%s err=%s — caching disabled for this call",
                            key, e,
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning("redis_cache.set_failed key=%s err=%s", key, e)

                return result

        # Expose helpers on the wrapped function for testing / manual eviction.
        wrapper.__cache_prefix__ = prefix  # type: ignore[attr-defined]
        wrapper.__cache_ttl__ = ttl  # type: ignore[attr-defined]
        wrapper.cache_invalidate = _make_invalidate(prefix, keygen)  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _make_invalidate(
    prefix: str, keygen: Callable[..., str],
) -> Callable[..., Awaitable[bool]]:
    """Builder for an async invalidate(*args, **kwargs) function.

    Use case: after a write that should bust the cache.
        await get_pesticide.cache_invalidate(name="...")
    """
    async def invalidate(*args: Any, **kwargs: Any) -> bool:
        client = get_redis()
        if client is None:
            return False
        try:
            suffix = keygen(*args, **kwargs)
        except Exception:  # noqa: BLE001
            return False
        key = f"{prefix}:{suffix}"
        try:
            n = await client.delete(key)
            return bool(n)
        except Exception as e:  # noqa: BLE001
            logger.warning("redis_cache.invalidate_failed key=%s err=%s", key, e)
            return False
    return invalidate


# ── Convenience: bulk invalidate by prefix ──────────────────────────────


async def invalidate_prefix(prefix: str) -> int:
    """Delete all keys matching ``{prefix}:*``. Returns count.

    For programmatic flushing — e.g. KAMIS schema change requires invalidating
    all kamis:* entries. Uses SCAN (non-blocking) instead of KEYS.
    """
    client = get_redis()
    if client is None:
        return 0
    pattern = f"{prefix}:*"
    deleted = 0
    cursor: Any = 0
    while True:
        cursor, keys = await client.scan(cursor=cursor, match=pattern, count=200)
        if keys:
            # Cap DELETE batch — redis-py forwards every key as a separate
            # command argument; a one-shot massive DEL can exceed the server's
            # proto-max-bulk-len.
            for i in range(0, len(keys), 500):
                batch = keys[i : i + 500]
                try:
                    deleted += await client.delete(*batch)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "redis_cache.bulk_delete_failed pattern=%s err=%s", pattern, e,
                    )
        # Cursor may come back as int (decode_responses=True) or bytes
        # (older redis-py, decode_responses=False). Normalise before the
        # exit check — a bytes-vs-int compare is always False and would
        # spin forever.
        if isinstance(cursor, (bytes, bytearray)):
            try:
                cursor = int(cursor)
            except ValueError:
                break
        if cursor == 0:
            break
    if deleted:
        logger.info("redis_cache.invalidate_prefix pattern=%s deleted=%d", pattern, deleted)
    return deleted
