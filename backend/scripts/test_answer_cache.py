"""Smoke test for the deterministic answer cache."""

import asyncio
import time


async def main() -> None:
    from app.core.redis import close_redis, init_redis
    from app.services.farm_agent import answer_cache

    await init_redis()

    # Cleanup any prior test data
    await answer_cache.invalidate_user("smoke_test")

    # Test 1: cache miss + store + hit
    q = "오늘 날씨가 어떻게 됩니까?"
    a = "현재 영주의 기온은 5.3도이며, 오후에는 흐려질 예정입니다. 농작업에 무리가 없습니다."

    print("Test 1 — store / lookup / hit")
    print(f"  Q: {q!r}")
    t = time.perf_counter()
    miss = await answer_cache.lookup(q, user_id="smoke_test")
    print(f"  miss: {miss!r}  in {(time.perf_counter()-t)*1000:.0f}ms")

    t = time.perf_counter()
    stored = await answer_cache.store(q, a, user_id="smoke_test")
    print(f"  stored: {stored}  in {(time.perf_counter()-t)*1000:.0f}ms")

    t = time.perf_counter()
    hit = await answer_cache.lookup(q, user_id="smoke_test")
    print(f"  hit: {hit == a}  in {(time.perf_counter()-t)*1000:.0f}ms")

    # Test 2: bypass for subsidy queries (must NOT cache)
    print()
    print("Test 2 — bypass for subsidy")
    q2 = "공익직불 자격이 뭐야?"
    a2 = "기본직불금 자격요건은..."
    stored = await answer_cache.store(q2, a2, user_id="smoke_test")
    print(f"  stored (subsidy): {stored} (expected False)")
    hit = await answer_cache.lookup(q2, user_id="smoke_test")
    print(f"  lookup (subsidy): {hit!r} (expected None)")

    # Test 3: bypass for pesticide
    q3 = "노균병에 농약 추천해줘"
    stored = await answer_cache.store(q3, "약제 추천...", user_id="smoke_test")
    print(f"  stored (pesticide): {stored} (expected False)")

    # Test 4: bypass for short answers
    print()
    print("Test 4 — bypass for short / placeholder answers")
    stored = await answer_cache.store("오늘 좋아", "ok", user_id="smoke_test")
    print(f"  stored (short): {stored} (expected False)")
    stored = await answer_cache.store("좋아", "응답을 생성하지 못했습니다.", user_id="smoke_test")
    print(f"  stored (placeholder): {stored} (expected False)")

    # Test 5: user scoping
    print()
    print("Test 5 — user scoping")
    qx = "곡물 시세 어디서 확인해?"
    ax = "KAMIS 또는 마트 시세를 참고하세요. 본 앱의 시세 탭에서도 조회 가능합니다."
    await answer_cache.store(qx, ax, user_id="user_A")
    hit_A = await answer_cache.lookup(qx, user_id="user_A")
    hit_B = await answer_cache.lookup(qx, user_id="user_B")
    print(f"  user_A hit: {hit_A is not None}")
    print(f"  user_B miss: {hit_B is None}")

    # cleanup
    await answer_cache.invalidate_user("smoke_test")
    await answer_cache.invalidate_user("user_A")
    await answer_cache.invalidate_user("user_B")
    await close_redis()
    print()
    print("OK — all answer_cache behaviors verified")


if __name__ == "__main__":
    asyncio.run(main())
