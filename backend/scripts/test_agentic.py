"""Quick smoke test for the agentic pipeline. Not a unit test — just exercises
the planner + CRAG + recursive ref expansion end-to-end against real Redis.
"""

from __future__ import annotations

import asyncio
import logging
import time


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from app.core.redis import close_redis, init_redis
    from app.services.subsidy.agentic import plan_query, run_agentic_search

    await init_redis()

    print("\n=== Planner tests ===")
    planner_qs = [
        "영농일지 안 쓰면 어떻게 돼?",     # factual
        "II-8 알려줘",                  # clause_lookup
        "오늘 날씨 어때?",                # off_topic
        "소농직불 자격이랑 부정수급 처벌 알려줘",  # multi_aspect
        "내가 다른 일로 4천만원 벌어도 직불금 신청 가능해?",  # rewrite test
    ]
    for q in planner_qs:
        t = time.perf_counter()
        p = await plan_query(q)
        ms = (time.perf_counter() - t) * 1000
        print(
            f"  {q!r:48s}\n"
            f"    intent={p.intent}  clause={p.clause_id}  subq_n={len(p.sub_queries)}\n"
            f"    rewritten={p.rewritten!r}\n"
            f"    ms={ms:.0f}"
        )

    print("\n=== Full agentic search ===")
    for q in [
        "영농일지 안 쓰면 어떻게 돼?",
        "내가 다른 일로 4천만원 벌어도 직불금 신청 가능해?",
        "별표 4 뭐가 있어?",
        "오늘 날씨 어때?",
    ]:
        t = time.perf_counter()
        r = await run_agentic_search(q, top_k=5)
        ms = (time.perf_counter() - t) * 1000
        clauses = [L.get("clause_id") for L in r.leaves]
        print(
            f"  {q!r:48s}\n"
            f"    plan.intent={r.plan.intent} crag={r.used_crag} "
            f"recursive={r.used_recursive} reformulated={r.reformulated}\n"
            f"    leaves={len(r.leaves)} clauses={clauses}\n"
            f"    ms={ms:.0f}"
        )

    await close_redis()


if __name__ == "__main__":
    asyncio.run(main())
