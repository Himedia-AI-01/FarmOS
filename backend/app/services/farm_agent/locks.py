"""Redis-backed locks and idempotency tokens for farm-agent side effects.

Why Redis (not Postgres):
    Both the user-agent lock and the actuator idempotency check need
    SET-NX-EX semantics with sub-millisecond latency. Postgres advisory
    locks work but each acquisition is a network round-trip, a planner
    call, and a row write — overkill for a 60-second TTL on a hot path.
    Redis SET nx_action 1 EX 300 NX is one round-trip, no parser, no WAL.

Why fail-OPEN (not fail-closed) when Redis is unavailable:
    Unlike LangCache (where a missing scope leaks data across users),
    these primitives only protect against concurrent / replayed actions.
    If Redis is down, the worst case for the lock is "two SSE sessions
    race"; for idempotency, "double-fire on retry." Both are degraded
    behaviours — not catastrophic. We log a warning and let the request
    through so a Redis outage doesn't take the whole agent offline.
    (For actuator safety, the IoT relay itself MUST also be idempotent
    on `user_id + control_type + action_id`. Defense in depth.)

References:
    - Redis distributed locks: https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/
    - Kleppmann critique on Redlock: only relevant for multi-master
      correctness scenarios. Single-instance fence-token usage is fine.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from app.core.redis import get_redis

logger = logging.getLogger(__name__)


# ── User-level "one running agent at a time" lock ───────────────────────────
#
# When a user opens the agent in two tabs, both establish SSE streams that
# share the same checkpointer thread_id. Concurrent writes corrupt the
# LangGraph state. The agent loop should hold this lock for the duration of
# a turn and release at the end (or via TTL on crash).

_USER_LOCK_PREFIX = "lock:agent:user:"
_USER_LOCK_DEFAULT_TTL_SEC = 90


# Sentinel returned when Redis is unavailable — distinguishes "no lock held
# but caller should proceed" from "lock held by us." Compared by identity in
# release/refresh so we don't accidentally try to del a string that looks
# like a real token. Defined here (before first use) to avoid a NameError
# if any caller invokes the lock helpers during module import.
_ALLOW_OPEN_TOKEN = "__ALLOW_OPEN__"


async def acquire_user_agent_lock(
    user_id: int | str,
    *,
    ttl_sec: int = _USER_LOCK_DEFAULT_TTL_SEC,
) -> str | None:
    """Try to acquire the per-user agent lock.

    Returns a lock token (string) on success — pass it back to
    `release_user_agent_lock` to delete only your own lock (compare-and-del).
    Returns None if another holder already has the lock.

    Caller policy: on None, return HTTP 409 (or push an SSE error event)
    so the client can show "다른 세션에서 에이전트가 실행 중입니다."
    """
    client = get_redis()
    if client is None:
        return _ALLOW_OPEN_TOKEN  # Redis disabled → fail-open

    token = secrets.token_hex(16)
    key = f"{_USER_LOCK_PREFIX}{user_id}"
    try:
        ok = await client.set(key, token, nx=True, ex=ttl_sec)
    except Exception as exc:  # noqa: BLE001 — Redis transient errors
        logger.warning("user_agent_lock.acquire_failed user=%s err=%s — failing open", user_id, exc)
        return _ALLOW_OPEN_TOKEN

    return token if ok else None


async def release_user_agent_lock(user_id: int | str, token: str | None) -> None:
    """Release the lock IFF we still own it (compare-and-delete via Lua).

    Calling with token=None or _ALLOW_OPEN_TOKEN is a no-op (we never held
    a real lock — Redis was unavailable).
    """
    if token is None or token == _ALLOW_OPEN_TOKEN:
        return
    client = get_redis()
    if client is None:
        return
    key = f"{_USER_LOCK_PREFIX}{user_id}"
    # Atomic compare-and-delete: only release if the stored token matches.
    # Without this, a slow handler whose lock TTL'd out could delete a
    # newer holder's lock.
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "  return redis.call('del', KEYS[1]) "
        "else return 0 end"
    )
    try:
        await client.eval(script, 1, key, token)
    except Exception as exc:  # noqa: BLE001
        logger.warning("user_agent_lock.release_failed user=%s err=%s", user_id, exc)


async def refresh_user_agent_lock(
    user_id: int | str,
    token: str | None,
    *,
    ttl_sec: int = _USER_LOCK_DEFAULT_TTL_SEC,
) -> bool:
    """Extend the TTL of a held lock. Use mid-stream for long-running turns.

    Returns True if the lock was refreshed (we still own it), False if
    someone else now holds it (caller should abort).
    """
    if token is None or token == _ALLOW_OPEN_TOKEN:
        return True
    client = get_redis()
    if client is None:
        return True
    key = f"{_USER_LOCK_PREFIX}{user_id}"
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "  return redis.call('expire', KEYS[1], ARGV[2]) "
        "else return 0 end"
    )
    try:
        result = await client.eval(script, 1, key, token, str(ttl_sec))
    except Exception as exc:  # noqa: BLE001
        logger.warning("user_agent_lock.refresh_failed user=%s err=%s", user_id, exc)
        return True
    return bool(result)


# ── Actuator idempotency token ──────────────────────────────────────────────
#
# IoT actuator commands (irrigation valve, ventilation fan) are real-world
# side effects. Without an idempotency key, double-tap on the approval
# button or an SSE replay could open the same valve twice, exceed the
# safe-spray window, etc. This wraps the action with `SET NX EX` so the
# second attempt with the same action_id is rejected.

_ACTION_IDEMPOTENCY_PREFIX = "idem:actuator:"
_ACTION_IDEMPOTENCY_DEFAULT_TTL_SEC = 300  # 5 min — long enough to cover retries


async def claim_actuator_action(
    action_id: str,
    *,
    user_id: int | str,
    control_type: str,
    ttl_sec: int = _ACTION_IDEMPOTENCY_DEFAULT_TTL_SEC,
) -> bool:
    """Reserve `action_id` for execution. Returns True if first time.

    The caller proceeds to fire the actuator only when this returns True.
    On False, the caller should respond with 409 and a "이미 처리된 요청"
    message so the user knows their original click went through.

    Key includes user_id + control_type to scope idempotency to *this
    user's command of this type* — different users hitting the same
    nominal action_id (e.g. defaulting to "auto") shouldn't collide.
    """
    if not action_id:
        return True  # No id = no idempotency — let the caller through
    client = get_redis()
    if client is None:
        # Fail-OPEN intentionally — see module docstring. The relay must
        # be the second line of defense against double-fire.
        logger.warning(
            "actuator_idempotency.redis_unavailable action=%s — proceeding (relay must dedupe)",
            action_id,
        )
        return True
    key = f"{_ACTION_IDEMPOTENCY_PREFIX}{user_id}:{control_type}:{action_id}"
    try:
        ok = await client.set(key, "1", nx=True, ex=ttl_sec)
    except Exception as exc:  # noqa: BLE001
        logger.warning("actuator_idempotency.failed action=%s err=%s", action_id, exc)
        return True  # fail-open
    return bool(ok)


# ── Convenience: stable action_id derivation ────────────────────────────────


def derive_action_id(*parts: Any) -> str:
    """Build a deterministic action_id from caller-supplied parts.

    For approve-action requests where the client doesn't send an explicit
    action_id, derive one from (session_id, control_type, action-payload-hash)
    so retries of the SAME logical action collide on the idempotency key.
    """
    import hashlib
    import json

    canonical = json.dumps(list(parts), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
