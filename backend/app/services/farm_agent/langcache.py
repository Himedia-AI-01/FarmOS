"""Redis LangCache 통합 — Farm Agent LLM 응답 의미 기반 캐시.

Pattern (Redis 권장 cache-before-LLM):
    cached = await lookup(question, user_id=...)
    if cached: return cached
    answer = await agent.ainvoke(...)
    await store(question, answer, user_id=...)

Scoping:
    attributes={"user_id": "<id>"} 로 사용자별 격리. 직불·진단 답변은 사용자
    농장 컨텍스트(작물·면적·지역)에 따라 다를 수 있어 글로벌 공유는 위험.
    user_id 기준 scoping 으로 동일 사용자의 paraphrase 만 hit.

Failure mode:
    어떤 예외도 절대 호출 측으로 전파하지 않는다 — lookup 은 None,
    store 는 silent. 캐시 장애가 에이전트 흐름을 막지 않게 한다.

Lifecycle:
    싱글턴 클라이언트 — FastAPI lifespan 에서 close() 호출. 미사용 시
    자동 비활성 (settings 미설정 또는 LANGCACHE_ENABLED=False).
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: Any | None = None
_init_attempted = False

# Runtime feature flag: flips False permanently the first time the LangCache
# server rejects an `attributes` payload with HTTP 400 "no attributes are
# configured for this cache." Subsequent calls then skip the attributes kwarg
# entirely (avoids one wasted round-trip per request).
#
# To get per-user scoping back, add a `user_id` (string) attribute in the Redis
# LangCache dashboard for this cache_id; until then answers are cache-shared
# across users in the same threshold band.
_supports_attributes: bool = True


def _is_attributes_unsupported_error(exc: Exception) -> bool:
    """Detect the LangCache 400 'attributes not configured' response.

    The SDK surfaces server errors as exceptions whose str() contains the JSON
    error body. We match conservatively on the distinctive detail string.
    """
    msg = str(exc)
    return "no attributes are configured for this cache" in msg


def _is_configured() -> bool:
    return bool(
        settings.LANGCACHE_ENABLED
        and settings.LANGCACHE_API_KEY
        and settings.LANGCACHE_CACHE_ID
        and settings.LANGCACHE_SERVER_URL
    )


def _get_client() -> Any | None:
    """Lazy singleton. None 반환 시 호출 측은 캐시 우회."""
    global _client, _init_attempted
    if _client is not None:
        return _client
    if _init_attempted:
        return None
    _init_attempted = True
    if not _is_configured():
        logger.info("langcache.disabled — settings 미충족 (api_key/cache_id/server_url)")
        return None
    try:
        from langcache import LangCache

        _client = LangCache(
            settings.LANGCACHE_SERVER_URL,
            cache_id=settings.LANGCACHE_CACHE_ID,
            api_key=settings.LANGCACHE_API_KEY,
        )
        logger.info(
            "langcache.enabled threshold=%.2f server=%s",
            settings.LANGCACHE_SIMILARITY_THRESHOLD,
            settings.LANGCACHE_SERVER_URL,
        )
        return _client
    except Exception as exc:  # noqa: BLE001 — 캐시 초기화 실패가 부팅 막지 않음
        logger.warning("langcache.init_failed err=%s", exc)
        return None


def _attrs(user_id: int | str | None) -> dict[str, str] | None:
    if user_id is None:
        return None
    return {"user_id": str(user_id)}


async def lookup(question: str, user_id: int | str | None = None) -> str | None:
    """의미 유사 답변을 찾으면 반환, 아니면 None.

    threshold 미달 또는 빈 결과는 모두 None. 어떤 예외도 흘려보내지 않는다.
    """
    if not question or not question.strip():
        return None
    client = _get_client()
    if client is None:
        return None
    global _supports_attributes
    try:
        attrs = _attrs(user_id) if _supports_attributes else None
        # SDK 는 attributes kwarg 를 지원 — 미지원 버전이면 TypeError 잡아서 fallback.
        try:
            if attrs is not None:
                result = await client.search_async(prompt=question, attributes=attrs)
            else:
                result = await client.search_async(prompt=question)
        except TypeError:
            result = await client.search_async(prompt=question)
        except Exception as exc:  # noqa: BLE001
            if _supports_attributes and _is_attributes_unsupported_error(exc):
                logger.warning(
                    "langcache.attributes_unsupported — Redis cache_id=%s lacks "
                    "user_id attribute schema. Disabling attributes for this "
                    "process and falling back to global cache. Add a 'user_id' "
                    "(string) attribute in the LangCache dashboard for per-user "
                    "isolation.",
                    settings.LANGCACHE_CACHE_ID,
                )
                _supports_attributes = False
                # Retry once without attributes so this request still benefits.
                result = await client.search_async(prompt=question)
            else:
                raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("langcache.lookup_failed err=%s", exc)
        return None

    match = _best_match(result)
    if match is None:
        return None

    similarity, response = match
    threshold = float(settings.LANGCACHE_SIMILARITY_THRESHOLD)
    if similarity < threshold:
        logger.info(
            "langcache.miss_below_threshold sim=%.3f threshold=%.3f user=%s",
            similarity, threshold, user_id,
        )
        return None
    logger.info("langcache.hit sim=%.3f user=%s", similarity, user_id)
    return response


def _best_match(result: Any) -> tuple[float, str] | None:
    """SDK 응답 형태 흡수 — `match`/`matches`/리스트/단일 객체 모두 처리.

    반환 (similarity, response). 추출 실패 시 None.
    """
    if result is None:
        return None
    candidates: list[Any] = []
    # 단일 match 속성 (튜토리얼 패턴)
    single = getattr(result, "match", None)
    if single is not None:
        candidates.append(single)
    # 다중 matches
    many = getattr(result, "matches", None) or getattr(result, "data", None)
    if many:
        candidates.extend(list(many))
    if not candidates and isinstance(result, list):
        candidates = result
    if not candidates and isinstance(result, dict):
        for key in ("match", "matches", "data"):
            val = result.get(key)
            if isinstance(val, list):
                candidates.extend(val)
            elif val is not None:
                candidates.append(val)

    best: tuple[float, str] | None = None
    for c in candidates:
        sim = _read(c, "similarity") or _read(c, "score") or 0.0
        resp = _read(c, "response") or _read(c, "answer") or ""
        try:
            sim_f = float(sim)
        except (TypeError, ValueError):
            continue
        if not resp:
            continue
        if best is None or sim_f > best[0]:
            best = (sim_f, resp)
    return best


def _read(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


async def store(
    question: str,
    answer: str,
    user_id: int | str | None = None,
) -> None:
    """답변을 캐시에 적재. 실패는 silent."""
    if not question or not answer:
        return
    if not question.strip() or not answer.strip():
        return
    client = _get_client()
    if client is None:
        return
    global _supports_attributes
    try:
        attrs = _attrs(user_id) if _supports_attributes else None
        try:
            if attrs is not None:
                await client.set_async(prompt=question, response=answer, attributes=attrs)
            else:
                await client.set_async(prompt=question, response=answer)
        except TypeError:
            await client.set_async(prompt=question, response=answer)
        except Exception as exc:  # noqa: BLE001
            if _supports_attributes and _is_attributes_unsupported_error(exc):
                logger.warning(
                    "langcache.attributes_unsupported — Redis cache_id=%s lacks "
                    "user_id attribute schema. Disabling attributes for this "
                    "process. Add a 'user_id' (string) attribute in the "
                    "LangCache dashboard for per-user isolation.",
                    settings.LANGCACHE_CACHE_ID,
                )
                _supports_attributes = False
                await client.set_async(prompt=question, response=answer)
            else:
                raise
        logger.info("langcache.stored user=%s qlen=%d alen=%d scoped=%s",
                    user_id, len(question), len(answer), _supports_attributes)
    except Exception as exc:  # noqa: BLE001
        logger.warning("langcache.store_failed err=%s", exc)


async def aclose() -> None:
    """FastAPI shutdown 훅에서 호출."""
    global _client
    if _client is None:
        return
    try:
        close_fn = getattr(_client, "aclose", None) or getattr(_client, "close", None)
        if close_fn is not None:
            res = close_fn()
            if hasattr(res, "__await__"):
                await res
    except Exception as exc:  # noqa: BLE001
        logger.warning("langcache.close_failed err=%s", exc)
    finally:
        _client = None
