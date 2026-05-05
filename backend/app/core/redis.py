"""Redis Stack 8.4+ async client + pool, scoped to subsidy RAG (and future shared use).

Why two clients on one pool:
    - HASH/JSON/text commands want strings → ``decode_responses=True``
    - Vector field bytes (raw float32) and FT.HYBRID's ``$query_vec`` parameter
      need raw bytes → ``decode_responses=False``
    Sharing the underlying ``ConnectionPool`` keeps total connection count bounded.

Why graceful disable:
    Existing modules (LangCache, future caches) treat missing Redis as "feature off".
    REDIS_URL="" → ``get_redis()`` returns None; callers check and fall back.

Why FT.HYBRID version probe at startup:
    Code paths branch on whether server-side hybrid is available. We probe once
    in lifespan and cache the result on ``app.state.redis_supports_hybrid``.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

import redis.asyncio as aioredis
from redis.asyncio.connection import ConnectionPool
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Module-level singletons (set by init_redis, cleared by close_redis) ────

@dataclass(frozen=True)
class RedisCapabilities:
    """What this Redis instance can do — probed once at startup."""

    redis_version: str
    search_module_version: str | None       # e.g. "80400" → 8.4.0
    supports_ft_hybrid: bool                # FT.HYBRID requires Search 8.4+
    supports_vectors: bool                  # FT.SEARCH with VECTOR field

    @property
    def search_major(self) -> int:
        if not self.search_module_version:
            return 0
        try:
            # Redis modules report version as packed int: 80400 = 8.4.0
            return int(self.search_module_version) // 10000
        except ValueError:
            return 0


_text_pool: ConnectionPool | None = None
_bin_pool: ConnectionPool | None = None
_text_client: aioredis.Redis | None = None
_bin_client: aioredis.Redis | None = None
_capabilities: RedisCapabilities | None = None


def is_enabled() -> bool:
    """REDIS_URL must be set for any Redis-backed feature."""
    return bool(settings.REDIS_URL)


async def init_redis() -> RedisCapabilities | None:
    """Create the connection pool, probe capabilities, return capabilities.

    Idempotent — safe to call from lifespan even if already initialised.
    Returns None if REDIS_URL is empty (Redis disabled).
    """
    global _text_pool, _bin_pool, _text_client, _bin_client, _capabilities

    if not is_enabled():
        logger.info("redis.disabled REDIS_URL is empty — subsidy RAG will use chroma fallback")
        return None

    if _text_pool is not None and _capabilities is not None:
        return _capabilities

    try:
        # Two pools — one decodes responses to str, one keeps raw bytes.
        # In redis-py 5.x, decode_responses is set on the ConnectionPool's
        # connections; passing it on the client when sharing a pool does NOT
        # re-decode. So we run two pools, each capped at half the budget.
        half = max(2, settings.REDIS_POOL_MAX_CONNECTIONS // 2)
        _text_pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=half,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SEC,
            socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT_SEC,
            decode_responses=True,
        )
        _bin_pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=half,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SEC,
            socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT_SEC,
            decode_responses=False,
        )
        _text_client = aioredis.Redis(connection_pool=_text_pool)
        _bin_client = aioredis.Redis(connection_pool=_bin_pool)

        # Health check — fail fast with a clear message if creds/host are wrong.
        await _text_client.ping()
    except RedisError as e:
        logger.error(
            "redis.init_failed url_host=%s err=%s — Redis features disabled, "
            "subsidy RAG falls back to chroma backend",
            _safe_host(settings.REDIS_URL),
            e,
        )
        await _shutdown_pool()
        return None

    _capabilities = await _probe_capabilities(_text_client)
    logger.info(
        "redis.ready host=%s redis=%s search=%s ft_hybrid=%s",
        _safe_host(settings.REDIS_URL),
        _capabilities.redis_version,
        _capabilities.search_module_version or "absent",
        _capabilities.supports_ft_hybrid,
    )
    return _capabilities


async def close_redis() -> None:
    """Tear down on lifespan exit. Safe to call when never initialised."""
    await _shutdown_pool()


async def _shutdown_pool() -> None:
    global _text_pool, _bin_pool, _text_client, _bin_client, _capabilities
    for client in (_text_client, _bin_client):
        if client is not None:
            try:
                await client.aclose()  # type: ignore[attr-defined] — redis-py 5+
            except Exception:  # noqa: BLE001 — shutdown best-effort
                pass
    for pool in (_text_pool, _bin_pool):
        if pool is not None:
            try:
                await pool.aclose()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
    _text_pool = None
    _bin_pool = None
    _text_client = None
    _bin_client = None
    _capabilities = None


# ── Accessors used by callers ─────────────────────────────────────────────


def get_redis() -> aioredis.Redis | None:
    """Text-decoded Redis client. None if disabled.

    Use for: HSET/HGET on string fields, FT.SEARCH text queries, EXPIRE, EXISTS,
    JSON.* commands, anything where you want str responses.
    """
    return _text_client


def get_redis_bytes() -> aioredis.Redis | None:
    """Raw-bytes Redis client (decode_responses=False). None if disabled.

    Use for: HSET of float32 embedding bytes, FT.HYBRID with $query_vec param,
    KNN searches that return vector payloads.
    """
    return _bin_client


def get_capabilities() -> RedisCapabilities | None:
    """What the connected Redis can do. None if not connected."""
    return _capabilities


def require_redis() -> aioredis.Redis:
    """Raise if Redis isn't available — for code paths that must use it.

    Subsidy code uses get_redis() with chroma fallback; this is for new
    Redis-only features that have no fallback.
    """
    client = get_redis()
    if client is None:
        raise RuntimeError(
            "Redis is required but REDIS_URL is empty or connection failed. "
            "Set REDIS_URL in .env or check startup logs for redis.init_failed."
        )
    return client


# ── Capability probing ────────────────────────────────────────────────────


async def _probe_capabilities(client: aioredis.Redis) -> RedisCapabilities:
    redis_version = ""
    try:
        info = await client.info("server")
        redis_version = str(info.get("redis_version", ""))
    except RedisError as e:
        logger.warning("redis.info_failed err=%s", e)

    search_ver: str | None = None
    try:
        # MODULE LIST returns list[dict] with name+ver fields. Different redis-py
        # versions return slightly different shapes; handle both.
        modules = await client.execute_command("MODULE", "LIST")
        for m in modules or []:
            # bytes-decoded: list of [b"name", b"search", b"ver", b"80400"]
            # or text-decoded dict: {"name": "search", "ver": "80400"}
            name, ver = _extract_module_name_ver(m)
            if name and name.lower() == "search":
                search_ver = ver
                break
    except RedisError as e:
        logger.warning("redis.module_list_failed err=%s", e)

    # FT.HYBRID requires Search 8.4 (packed int 80400+).
    supports_hybrid = False
    if search_ver:
        try:
            supports_hybrid = int(search_ver) >= 80400
        except ValueError:
            supports_hybrid = False

    return RedisCapabilities(
        redis_version=redis_version,
        search_module_version=search_ver,
        supports_ft_hybrid=supports_hybrid,
        supports_vectors=bool(search_ver),  # any Search version supports VECTOR fields
    )


def _extract_module_name_ver(m: object) -> tuple[str | None, str | None]:
    """MODULE LIST output normalisation across redis-py versions/decode modes."""
    if isinstance(m, dict):
        name = m.get("name") or m.get(b"name")
        ver = m.get("ver") or m.get(b"ver")
        return _to_str(name), _to_str(ver)
    if isinstance(m, list):
        # Flatten alternating [k, v, k, v, ...] into dict
        d: dict[str, str] = {}
        for i in range(0, len(m) - 1, 2):
            k = _to_str(m[i])
            v = _to_str(m[i + 1])
            if k:
                d[k] = v or ""
        return d.get("name"), d.get("ver")
    return None, None


def _to_str(x: object) -> str | None:
    if x is None:
        return None
    if isinstance(x, bytes):
        try:
            return x.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return str(x)


def _safe_host(url: str) -> str:
    """Strip credentials from URL for logging."""
    if "@" not in url:
        return url
    if "://" in url:
        scheme, rest = url.split("://", 1)
    else:
        # No scheme but credentials present (e.g. "user:pass@host:6379") — still
        # need to redact. Use a placeholder scheme so the output stays parseable.
        scheme, rest = "redis", url
    _, host = rest.rsplit("@", 1)
    return f"{scheme}://{host}"


# ── Lifespan helper (called from app/main.py) ─────────────────────────────


@asynccontextmanager
async def redis_lifespan():
    """Context manager for FastAPI lifespan integration.

    Use as:
        async with redis_lifespan() as caps:
            app.state.redis_caps = caps
            yield
    """
    caps = await init_redis()
    try:
        yield caps
    finally:
        await close_redis()
