"""Deterministic exact-match answer cache for farm_agent.

LangCache (의미 캐시) 와 비교:
    LangCache : 임베딩 유사도 기반 — paraphrase 도 hit. ~50-200ms (lookup).
    answer_cache: sha1(질문+사용자) exact 매칭 — paraphrase 미스. ~3ms (lookup).

언제 가치 있나:
    - 모바일 앱 더블탭 / F5 새로고침 — 동일 텍스트 반복 질의 (현장에서 빈번)
    - 클라이언트의 자동 polling / retry
    - SSE 스트림 끊겨서 클라이언트가 재요청
    - 공유 답변 — 같은 questions 가 여러 사용자에게 인기 있을 때

순서:
    fast_path → answer_cache (exact) → langcache (semantic) → LLM (Deep Agent)

bypass:
    langcache._BYPASS_PATTERNS 와 동일한 룰을 그대로 사용.
    - 직불/진단/농약/IoT/센서 관련 질의는 신선도 보장이 더 중요해 caching 안 함.
    - 조항 인용은 사용자 농장 컨텍스트마다 재도출 필요 (감사·정확성 이유).

scoping:
    user_id 를 키에 포함 (langcache 와 동일 정책). 사용자 별로 격리.

저장 정책:
    - 빈 답변 / 너무 짧은 답변 (< 20자) 은 미저장.
    - "응답을 생성하지 못했습니다" 같은 placeholder 답변은 미저장.
    - 비-bypass 카테고리 + 충분한 길이 + 정상 응답 시에만 저장.

키 설계:
    "agent:answer:{user_id}:{sha1(normalized_question)}"

TTL:
    1시간 (default). LangCache 보다 짧음 — exact 매칭이 더 공격적이라 stale 위험 ↑.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.core.config import settings
from app.core.redis import get_redis
from app.services.farm_agent.langcache import should_bypass

logger = logging.getLogger(__name__)

# 짧은 답변은 caching 가치 낮음 + placeholder/error 답변 차단.
MIN_ANSWER_CHARS = 20
# 너무 긴 답변은 Redis 메모리 부담. 4KB 이상은 저장 안 함 (사용자에게 가는 답변은
# 일반적으로 200-2000자).
MAX_ANSWER_CHARS = 4096
# 1시간 — LangCache(threshold 기반)보다 짧게. exact match 는 더 aggressive 라
# stale 답변 위험 ↑. 1시간이면 모바일 polling/retry 패턴은 충분히 커버.
DEFAULT_TTL_SEC = 3600

# Placeholder/error 답변 (사용자에게 노출되어도 캐시할 가치 없음)
_PLACEHOLDER_PHRASES = (
    "응답을 생성하지 못했습니다",
    "에이전트 응답 생성 중 오류",
    "잠시 후 다시 시도",
    "현재 응답할 수 없",
)


def _normalize(text: str) -> str:
    """Whitespace 만 정규화 — 의미적 paraphrase 는 LangCache 가 잡음."""
    return " ".join(text.split())


def _key(question: str, user_id: int | str | None) -> str:
    """Deterministic cache key. user_id 가 None 이면 'anon' bucket."""
    norm = _normalize(question)
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()
    uid = str(user_id) if user_id is not None else "anon"
    return f"agent:answer:{uid}:{digest}"


def _is_unfit_answer(answer: str | None) -> bool:
    """저장 가능한 답변인지 판정. True 면 저장 skip."""
    if not answer:
        return True
    a = answer.strip()
    if len(a) < MIN_ANSWER_CHARS:
        return True
    if len(a) > MAX_ANSWER_CHARS:
        return True
    for ph in _PLACEHOLDER_PHRASES:
        if ph in a:
            return True
    return False


# ── Public API ──────────────────────────────────────────────────


async def lookup(question: str, user_id: int | str | None = None) -> str | None:
    """Exact-text answer 캐시 조회.

    bypass 룰에 걸리거나 Redis 비활성/오류 시 None 반환 → 호출 측은 정상 흐름 진행.
    """
    if not question or not question.strip():
        return None
    if should_bypass(question):
        logger.info("answer_cache.bypass user=%s", user_id)
        return None

    client = get_redis()
    if client is None:
        return None

    key = _key(question, user_id)
    try:
        cached = await client.get(key)
    except Exception as e:  # noqa: BLE001 — cache 장애가 응답 흐름 막지 않음
        logger.warning("answer_cache.lookup_failed key=%s err=%s", key, e)
        return None
    if cached:
        logger.info("answer_cache.hit user=%s qlen=%d alen=%d", user_id,
                    len(question), len(cached))
        return cached
    return None


async def store(
    question: str,
    answer: str,
    user_id: int | str | None = None,
    *,
    ttl: int = DEFAULT_TTL_SEC,
) -> bool:
    """Exact-text answer 저장. 저장 성공 여부 반환 (호출 측은 보통 무시).

    저장 안 되는 경우:
        - bypass 룰에 걸림 (직불/진단/농약/IoT 등)
        - 답변이 너무 짧거나 placeholder/error
        - Redis 비활성/오류
    """
    if should_bypass(question):
        return False
    if _is_unfit_answer(answer):
        return False
    client = get_redis()
    if client is None:
        return False

    key = _key(question, user_id)
    try:
        await client.setex(key, ttl, answer)
        logger.info("answer_cache.store user=%s qlen=%d alen=%d ttl=%d",
                    user_id, len(question), len(answer), ttl)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("answer_cache.store_failed key=%s err=%s", key, e)
        return False


async def invalidate_user(user_id: int | str) -> int:
    """특정 사용자의 모든 캐시 키 삭제. 사용자가 답변 품질에 불만을 표시하거나
    프로필이 큰 변화를 겪었을 때 호출.

    Returns: 삭제된 키 수.
    """
    client = get_redis()
    if client is None:
        return 0
    pattern = f"agent:answer:{user_id}:*"
    deleted = 0
    cursor: Any = 0
    try:
        while True:
            cursor, keys = await client.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                deleted += await client.delete(*keys)
            # Normalise bytes cursor (decode_responses=False clients) so the
            # `cursor == 0` exit doesn't loop forever on `b"0"`.
            if isinstance(cursor, (bytes, bytearray)):
                try:
                    cursor = int(cursor)
                except ValueError:
                    break
            if cursor == 0:
                break
    except Exception as e:  # noqa: BLE001
        logger.warning("answer_cache.invalidate_user_failed user=%s err=%s", user_id, e)
        return deleted
    if deleted:
        logger.info("answer_cache.invalidate_user user=%s deleted=%d", user_id, deleted)
    return deleted
