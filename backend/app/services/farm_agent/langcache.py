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
import re
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


# Intent-scoped bypass — questions whose answers depend on per-user farm
# context (profile, journal, eligibility) or carry regulatory/safety risk
# (subsidy citations, pesticide dosing) MUST NOT be served from a semantic
# cache. Even with user_id scoping, two different questions from the same
# user can match above the similarity threshold and return the wrong answer
# (e.g. Q: "공익직불 자격" hits cached A: "KAMIS 시세" when threshold is loose).
#
# Match is intentionally broad (Korean keyword OR-list) — false-positive
# bypasses cost a cache miss; false-negative cache hits cost a wrong answer
# in domains where wrong answers carry real consequences (subsidy denial,
# pesticide misuse). Trade-off favours the miss.
_BYPASS_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Subsidy / compliance / regulation — citations must be re-derived per turn.
    re.compile(
        r"(공익직불|직불금|직불\s*제도|소농직불|면적직불|선택직불|"
        r"준수사항|시행지침|영농기록|영농일지|농사일지|기록부|작업일지|"
        r"부정수급|감액|환수|자격|신청|보조금|지원금|정책자금|"
        r"농업경영체|농지\s*형상|화학비료|영농폐기물)"
    ),
    # Pest / disease diagnosis & pesticide recommendations — safety-critical.
    re.compile(
        r"(병해충|방제|진단|농약|살포|희석배수|살균제|살충제|약제|"
        r"노균병|흰가루병|탄저병|잿빛곰팡이|역병|무름병|진딧물|응애|"
        r"총채벌레|굼벵이|나방|깍지벌레|선충|친환경\s*방제)"
    ),
    # Farm-context-dependent reads — answer varies by user profile / records.
    re.compile(
        r"(내\s*농장|우리\s*농장|내\s*작물|내\s*땅|내\s*밭|"
        r"농장\s*정보|프로필|일지|기록|영농\s*기록|"
        r"IoT|센서|제어\s*이력|관수|환기|급수|온실\s*제어)"
    ),
)


def should_bypass(question: str | None) -> bool:
    """Return True if the question must skip the semantic cache.

    Use at every lookup AND store site — bypassing only on lookup would still
    pollute the cache with subsidy/diagnosis answers that future unrelated
    questions could match.
    """
    if not question:
        return False
    text = question.strip()
    if not text:
        return False
    # Cap regex input — a 100KB pasted question would otherwise re.search every
    # bypass pattern across the whole blob. The patterns target keyword tokens
    # near the head/tail of a question so 4 KB is well past anything the UI
    # actually sends.
    if len(text) > 4096:
        text = text[:2048] + text[-2048:]
    return any(p.search(text) for p in _BYPASS_PATTERNS)

_client: Any | None = None
_init_attempted = False

# Runtime feature flag: flips False permanently the first time the LangCache
# server rejects an `attributes` payload with HTTP 400 "no attributes are
# configured for this cache."
#
# IMPORTANT — fail-closed: when this is False the cache is **disabled entirely**
# (lookup returns None, store no-ops). Previously we degraded to a global cache
# without user_id scoping, which leaked answers across users (subsidy/diagnosis
# answers are farm-context-dependent — sharing them is a real-money correctness
# bug, not just a privacy concern).
#
# To re-enable: add a `user_id` (string) attribute in the Redis LangCache
# dashboard for this cache_id. The first user_id-scoped request after that will
# re-flip _supports_attributes to True automatically (we don't sticky the
# False — restart fixes it; see _client = None reset path).
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
    if should_bypass(question):
        logger.info("langcache.bypass_intent op=lookup user=%s", user_id)
        return None
    client = _get_client()
    if client is None:
        return None
    global _supports_attributes
    # Fail-closed: if the cache can't scope by user_id, refuse to serve.
    # Crossing user boundaries on cached subsidy/diagnosis answers is a
    # correctness bug; degrading to global cache hides it.
    if not _supports_attributes:
        return None
    if user_id is None:
        # No user context = no isolation key. Skip rather than risk leak.
        return None
    try:
        attrs = _attrs(user_id)
        # SDK 는 attributes kwarg 를 지원 — 미지원 버전이면 TypeError 잡아서 fallback.
        try:
            result = await client.search_async(prompt=question, attributes=attrs)
        except TypeError:
            # SDK doesn't accept attributes kwarg → cannot scope → bail out.
            logger.warning(
                "langcache.sdk_no_attributes_kwarg — install langcache SDK with "
                "attributes support; cache disabled until then."
            )
            _supports_attributes = False
            return None
        except Exception as exc:  # noqa: BLE001
            if _is_attributes_unsupported_error(exc):
                logger.warning(
                    "langcache.attributes_unsupported — Redis cache_id=%s lacks "
                    "user_id attribute schema. **Disabling cache entirely** "
                    "until restarted. Add a 'user_id' (string) attribute in the "
                    "LangCache dashboard for per-user isolation, then restart "
                    "the backend.",
                    settings.LANGCACHE_CACHE_ID,
                )
                _supports_attributes = False
                return None
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
    if should_bypass(question):
        logger.info("langcache.bypass_intent op=store user=%s", user_id)
        return
    client = _get_client()
    if client is None:
        return
    global _supports_attributes
    # Fail-closed on store too — never write to the unscoped cache, since a
    # poisoned global entry will leak to every other user on subsequent reads.
    if not _supports_attributes:
        return
    if user_id is None:
        return
    try:
        attrs = _attrs(user_id)
        try:
            await client.set_async(prompt=question, response=answer, attributes=attrs)
        except TypeError:
            logger.warning(
                "langcache.sdk_no_attributes_kwarg — install langcache SDK with "
                "attributes support; cache disabled until then."
            )
            _supports_attributes = False
            return
        except Exception as exc:  # noqa: BLE001
            if _is_attributes_unsupported_error(exc):
                logger.warning(
                    "langcache.attributes_unsupported — Redis cache_id=%s lacks "
                    "user_id attribute schema. **Disabling cache entirely** "
                    "until restarted. Add a 'user_id' (string) attribute in "
                    "the LangCache dashboard for per-user isolation, then "
                    "restart the backend.",
                    settings.LANGCACHE_CACHE_ID,
                )
                _supports_attributes = False
                return
            raise
        logger.info(
            "langcache.stored user=%s qlen=%d alen=%d", user_id, len(question), len(answer)
        )
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
