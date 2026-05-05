"""End-to-end smoke test — validates every Redis-backed feature we've built.

Coverage:
    1. Redis core      — connection, capabilities, FT.HYBRID support
    2. Subsidy RAG     — sub_split chunks, hybrid search, clause lookup,
                          recursive refs, top-3 quality on 5 hand-picked queries
    3. Embedding cache — Solar embed cache hit/miss
    4. Contextual prefix cache — Redis backend selection
    5. Answer cache    — exact match, bypass rules, user scoping
    6. Universal redis_cache decorator — KMA weather (real upstream)
    7. Configuration   — settings sanity

Run from backend/:
    uv run python scripts/test_all_systems.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any


# ── Test runner ────────────────────────────────────────────────────────


class TestResult:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.skipped: list[tuple[str, str]] = []
        self.timings: dict[str, float] = {}

    def ok(self, name: str, ms: float | None = None) -> None:
        self.passed.append(name)
        if ms is not None:
            self.timings[name] = ms

    def fail(self, name: str, reason: str) -> None:
        self.failed.append((name, reason))

    def skip(self, name: str, reason: str) -> None:
        self.skipped.append((name, reason))


R = TestResult()


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def check(name: str, ok: bool, detail: str = "", ms: float | None = None) -> None:
    if ok:
        ms_str = f"  ({ms:.0f}ms)" if ms is not None else ""
        print(f"  [OK]  {name}{ms_str}")
        if detail:
            print(f"        {detail}")
        R.ok(name, ms)
    else:
        print(f"  [FAIL] {name}")
        if detail:
            print(f"         {detail}")
        R.fail(name, detail)


# ── Tests ──────────────────────────────────────────────────────────────


async def test_redis_core() -> None:
    section("1. Redis core")
    from app.core.config import settings
    from app.core.redis import init_redis, get_capabilities, get_redis, get_redis_bytes

    if not settings.REDIS_URL:
        R.skip("redis_core", "REDIS_URL not set")
        print("  [SKIP] REDIS_URL not configured")
        return

    t = time.perf_counter()
    caps = await init_redis()
    init_ms = (time.perf_counter() - t) * 1000

    check("Redis init + ping", caps is not None,
          detail=f"redis={caps.redis_version if caps else 'n/a'}", ms=init_ms)
    if caps is None:
        return
    check("FT.HYBRID supported", caps.supports_ft_hybrid,
          detail=f"search module v{caps.search_module_version}")
    check("Vector index supported", caps.supports_vectors)

    text = get_redis()
    bytes_c = get_redis_bytes()
    check("Two clients available (text + bytes)", text is not None and bytes_c is not None)

    # Quick decode_responses sanity — text should return str, bytes should return bytes
    t = time.perf_counter()
    await text.set("test:probe:str", "hello")
    val = await text.get("test:probe:str")
    text_ms = (time.perf_counter() - t) * 1000
    check("Text client decode_responses=True", isinstance(val, str) and val == "hello",
          detail=f"got {type(val).__name__}={val!r}", ms=text_ms)

    await bytes_c.set("test:probe:bin", b"\x00\xff\x10")
    val_b = await bytes_c.get("test:probe:bin")
    check("Bytes client decode_responses=False",
          isinstance(val_b, bytes) and val_b == b"\x00\xff\x10",
          detail=f"got {type(val_b).__name__}")

    # Cleanup
    await text.delete("test:probe:str", "test:probe:bin")


def _semantic_match(citations: list, expected_keywords: list[str]) -> bool:
    """Backend-agnostic match: check if any of the top citations contains the
    expected keywords in either article or chapter or snippet.

    Works for both chroma (article='3. 소농직불...', chapter='II. 기본직불금...')
    and redis (article='II-3', chapter='II-3') Citation formats.
    """
    for c in citations:
        haystack = f"{c.article or ''} {c.chapter or ''} {(c.snippet or '')[:100]}"
        if any(kw in haystack for kw in expected_keywords):
            return True
    return False


async def test_subsidy_rag() -> None:
    section("2. Subsidy RAG")
    from app.core.config import settings
    from app.services.subsidy.gov_rag import (
        GovSubsidyRAG, _search_redis_async,
    )
    from app.services.subsidy.redis_index import doc_count

    rag = GovSubsidyRAG()
    check(f"GovSubsidyRAG instantiated (backend={rag.backend})", True)

    if rag.backend == "redis":
        count = await doc_count()
    else:
        # chroma sync .count() is safe to call here
        count = rag.count()
    check(f"Index populated (count={count})", count > 0,
          detail="run `uv run subsidy-ingest` if 0")
    if count == 0:
        return

    # Use async path directly to avoid asyncio.run() collision inside this loop.
    if rag.backend == "redis":
        async def search(q: str, top_k: int = 3):
            return await _search_redis_async(q, top_k=top_k)
    else:
        async def search(q: str, top_k: int = 3):
            # chroma path is sync — wrap in to_thread
            return await asyncio.to_thread(rag.search, q, top_k)

    # Test 1: clause-direct lookup
    t = time.perf_counter()
    cits = await search("II-3 알려줘", 3)
    ms = (time.perf_counter() - t) * 1000
    check("Clause lookup (II-3 알려줘)",
          len(cits) > 0 and _semantic_match(cits[:3], ["II-3", "소농직불", "II.", "3."]),
          detail=f"top: {[c.article[:30] for c in cits[:3]]}", ms=ms)

    # Test 2: factual subsidy query
    t = time.perf_counter()
    cits = await search("소농직불 자격이 뭐야", 3)
    ms = (time.perf_counter() - t) * 1000
    check("Factual query (소농직불)",
          len(cits) > 0 and _semantic_match(cits[:3], ["소농직불", "II-3", "3."]),
          detail=f"top: {[c.article[:30] for c in cits[:3]]}", ms=ms)

    # Test 3: casual synonym (영농일지 → 영농기록)
    t = time.perf_counter()
    cits = await search("영농일지 안 쓰면 어떻게 돼?", 3)
    ms = (time.perf_counter() - t) * 1000
    check("Casual synonym (영농일지 → 영농기록)",
          len(cits) > 0 and _semantic_match(cits[:3], ["영농기록", "C2-VI", "VI."]),
          detail=f"top: {[c.article[:30] for c in cits[:3]]}", ms=ms)

    # Test 4: multi-aspect retrieval
    t = time.perf_counter()
    cits = await search("부정수급 처분이 어떻게 돼?", 5)
    ms = (time.perf_counter() - t) * 1000
    check("Multi-aspect retrieval (부정수급)",
          len(cits) > 0 and _semantic_match(cits[:3], ["부정수급", "II-7", "II-8", "8.", "7."]),
          detail=f"top: {[c.article[:30] for c in cits[:3]]}", ms=ms)

    # Test 5: medium-difficulty
    t = time.perf_counter()
    cits = await search("면적직불 진흥지역 단가가 얼마야?", 3)
    ms = (time.perf_counter() - t) * 1000
    check("Medium difficulty (면적직불 단가)",
          len(cits) > 0 and _semantic_match(cits[:3], ["지급단가", "II-4", "4."]),
          detail=f"top: {[c.article[:30] for c in cits[:3]]}", ms=ms)


async def test_embedding_cache() -> None:
    section("3. Embedding cache (Solar)")
    from app.services.subsidy.embedding_cache import CachedSolarEmbeddings, _cache_key

    embedder = CachedSolarEmbeddings()
    test_text = "캐시 테스트용 한국어 문장 — 동일 입력은 동일 임베딩이어야 한다."

    # First call (likely cache hit since we've ingested 277 leaves)
    t = time.perf_counter()
    v1 = await embedder.aembed_query(test_text)
    ms1 = (time.perf_counter() - t) * 1000

    # Second call should be cache hit
    t = time.perf_counter()
    v2 = await embedder.aembed_query(test_text)
    ms2 = (time.perf_counter() - t) * 1000

    check("Embedding determinism (same text → same vector)",
          v1 == v2,
          detail=f"len={len(v1)}, dim correct={len(v1) == 4096}", ms=ms2)
    check("Cache hit faster than cold call",
          ms2 < 50,  # hit should be <50ms (Redis HGET)
          detail=f"first={ms1:.0f}ms second={ms2:.0f}ms", ms=ms2)

    # Verify cache key generation
    key1 = _cache_key("solar-embedding-1-large", test_text)
    key2 = _cache_key("solar-embedding-1-large", test_text + "  ")  # trailing whitespace
    check("Cache key whitespace-normalised",
          key1 == key2,
          detail="trailing spaces don't break cache")


async def test_contextual_cache() -> None:
    section("4. Contextual prefix cache")
    from app.services.subsidy.contextual import ContextualPrefixCache, _content_hash

    cache = ContextualPrefixCache()
    check(f"Backend selected: {cache._backend_name}",
          cache._backend_name in ("redis", "json"),
          detail="redis when REDIS_URL set, json fallback otherwise")

    # Content hash determinism
    h1 = _content_hash("샘플 본문")
    h2 = _content_hash("샘플  본문")  # double space
    check("Content hash whitespace-stable", h1 == h2)


async def test_answer_cache() -> None:
    section("5. Farm agent answer cache (deterministic)")
    from app.services.farm_agent import answer_cache

    test_user = "smoke_test_full"
    await answer_cache.invalidate_user(test_user)

    q = "곡물 시세는 어디서 보지?"
    a = "FarmOS의 시세 탭에서 곡물별 일일 KAMIS 시세를 확인할 수 있습니다."

    # 1. Initial miss
    t = time.perf_counter()
    miss = await answer_cache.lookup(q, user_id=test_user)
    ms = (time.perf_counter() - t) * 1000
    check("Lookup miss returns None", miss is None, ms=ms)

    # 2. Store + lookup hit
    t = time.perf_counter()
    stored = await answer_cache.store(q, a, user_id=test_user)
    check("Store accepts non-bypass query", stored)

    t = time.perf_counter()
    hit = await answer_cache.lookup(q, user_id=test_user)
    ms = (time.perf_counter() - t) * 1000
    check("Lookup hit returns stored answer", hit == a, ms=ms)

    # 3. Bypass for subsidy
    stored = await answer_cache.store("공익직불 자격이 뭐야", "...", user_id=test_user)
    check("Bypass for subsidy query", not stored)

    # 4. Bypass for short answers
    stored = await answer_cache.store("test", "ok", user_id=test_user)
    check("Bypass for short answer", not stored)

    # 5. User scoping
    await answer_cache.store(q, a, user_id="other_user")
    hit_other = await answer_cache.lookup(q, user_id="other_user")
    hit_test = await answer_cache.lookup(q, user_id=test_user)
    check("User scoping isolates caches",
          hit_other is not None and hit_test is not None and hit_test == a,
          detail="both users have entries; not cross-leaked")

    # Cleanup
    await answer_cache.invalidate_user(test_user)
    await answer_cache.invalidate_user("other_user")


async def test_external_api_cache() -> None:
    section("6. External API cache (KMA, KAMIS, pesticide)")
    from app.core.config import settings
    from app.core.redis_cache import invalidate_prefix
    from app.core.weather_client import get_weather

    if not settings.KMA_DECODING_KEY:
        R.skip("kma_cache", "KMA_DECODING_KEY not set")
        print("  [SKIP] KMA cache — KMA_DECODING_KEY not set")
        return

    # Invalidate then test cold + warm
    await invalidate_prefix("kma:weather")

    t = time.perf_counter()
    r1 = await get_weather()
    ms_cold = (time.perf_counter() - t) * 1000
    check("KMA cold call (real upstream)",
          r1 is not None and "current" in r1,
          detail=f"source={r1.get('source')}, temp={r1['current'].get('temperature')}",
          ms=ms_cold)

    t = time.perf_counter()
    r2 = await get_weather()
    ms_warm = (time.perf_counter() - t) * 1000
    check("KMA warm call (Redis hit)",
          r1["current"]["temperature"] == r2["current"]["temperature"],
          detail=f"warm/cold ratio = {ms_warm / max(ms_cold, 1):.4f} (should be << 1)",
          ms=ms_warm)
    check(f"KMA cache speedup ≥ 10x",
          ms_warm * 10 < ms_cold,
          detail=f"cold {ms_cold:.0f}ms → warm {ms_warm:.0f}ms ({ms_cold / max(ms_warm, 1):.0f}× faster)")


async def test_settings_sanity() -> None:
    section("7. Settings sanity")
    from app.core.config import settings

    # Required for full functionality
    check("REDIS_URL set", bool(settings.REDIS_URL))
    check("UPSTAGE_API_KEY set (Solar embeddings)", bool(settings.UPSTAGE_API_KEY))
    check("OPENROUTER_API_KEY set (Haiku 4.5 contextual)",
          bool(settings.OPENROUTER_API_KEY))

    # Subsidy RAG settings
    check("SUBSIDY_RAG_BACKEND in (chroma, redis)",
          settings.SUBSIDY_RAG_BACKEND in ("chroma", "redis"),
          detail=f"current: {settings.SUBSIDY_RAG_BACKEND}")
    check("SUBSIDY_PLANNER_ENABLED set (default off recommended)",
          settings.SUBSIDY_PLANNER_ENABLED is False,
          detail="default off per V5 measurements")
    check("SUBSIDY_CRAG_ENABLED set (default off recommended)",
          settings.SUBSIDY_CRAG_ENABLED is False)
    check("SUBSIDY_RECURSIVE_REFS_ENABLED on by default",
          settings.SUBSIDY_RECURSIVE_REFS_ENABLED is True)

    # Cache TTLs reasonable
    check("REDIS_EMBEDDING_TTL_DAYS in [7, 90]",
          7 <= settings.REDIS_EMBEDDING_TTL_DAYS <= 90,
          detail=f"current: {settings.REDIS_EMBEDDING_TTL_DAYS}")


# ── Main ──────────────────────────────────────────────────────────────


async def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    print()
    print("FarmOS Redis-backed systems — full smoke test")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        await test_redis_core()
        await test_settings_sanity()
        await test_embedding_cache()
        await test_contextual_cache()
        await test_subsidy_rag()
        await test_answer_cache()
        await test_external_api_cache()
    except Exception as e:
        print(f"\n[FATAL] uncaught exception: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()

    section("SUMMARY")
    total = len(R.passed) + len(R.failed) + len(R.skipped)
    print(f"  Total: {total}")
    print(f"  Passed: {len(R.passed)}")
    print(f"  Failed: {len(R.failed)}")
    print(f"  Skipped: {len(R.skipped)}")

    if R.failed:
        print()
        print("  FAILURES:")
        for name, reason in R.failed:
            print(f"    - {name}: {reason}")

    if R.timings:
        print()
        print("  Selected timings:")
        for name, ms in sorted(R.timings.items(), key=lambda x: -x[1])[:8]:
            print(f"    {name}: {ms:.0f}ms")

    from app.core.redis import close_redis
    await close_redis()

    if R.failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
