"""Smoke test the redis_cached decorator on real upstream APIs."""

import asyncio
import time


async def main() -> None:
    from app.core.redis import close_redis, init_redis
    from app.core.redis_cache import invalidate_prefix
    from app.core.weather_client import get_weather

    await init_redis()
    await invalidate_prefix("kma:weather")

    print("Weather call 1 (cold — KMA upstream):")
    t = time.perf_counter()
    r1 = await get_weather()
    src = r1.get("source")
    temp = r1["current"]["temperature"]
    print(f"  source={src}, temp={temp}, in {(time.perf_counter()-t)*1000:.0f}ms")

    print("Weather call 2 (warm — Redis hit):")
    t = time.perf_counter()
    r2 = await get_weather()
    print(f"  source={r2.get('source')}, in {(time.perf_counter()-t)*1000:.0f}ms")

    assert r1["current"]["temperature"] == r2["current"]["temperature"]
    print("Match:", r1["current"] == r2["current"])

    await close_redis()


if __name__ == "__main__":
    asyncio.run(main())
