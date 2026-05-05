"""ReasoningBank — trajectory recording + strategy distillation.

Background:
  Static memory (AGENTS.md domain knowledge + STRATEGIES.md operator-curated
  strategies) is auto-injected via MemoryMiddleware. This module adds the
  *dynamic* half of the ReasoningBank pattern (Google Cloud AI 2025):

    1. Record each agent run as a trajectory row (query, route, tools, verdict).
    2. Periodically distill recent trajectories into new STRATEGIES.md entries
       using an LLM-side summarizer. Both successful AND failed runs feed in —
       failures often surface the most actionable lessons.
    3. The next request reads the appended STRATEGIES.md via MemoryMiddleware,
       closing the self-improvement loop.

Embedding-based retrieval (top-K relevant strategies per query) is the
next-stage upgrade; for now the file stays small enough that full injection is
cheaper than embedding lookup. Add HNSW retrieval when STRATEGIES.md > 50 KB.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, async_session

logger = logging.getLogger(__name__)


# ── Trajectory model ────────────────────────────────────────────────────────


class FarmAgentTrajectory(Base):
    """단일 에이전트 실행 트라젝토리.

    트라젝토리 = 사용자 질문 한 건이 어떻게 처리됐는지의 압축 기록.
    distill_strategies() 가 이 행들을 읽어 STRATEGIES.md 에 새 패턴을 추가한다.

    크기 관리: 30일 이상 된 trajectory 는 distill 시 함께 정리. PII 는 query 본문에
    포함될 수 있으니 외부 노출 시 주의 (본 테이블은 내부 분석 전용).
    """

    __tablename__ = "farm_agent_trajectories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    route: Mapped[str] = mapped_column(
        String(40), nullable=False,
        # "orchestrator", "diagnosis", "subsidy", "subsidy_escalated",
        # "farm_data", "briefing"
    )
    tools_used: Mapped[str] = mapped_column(Text, default="[]")  # JSON array
    response_summary: Mapped[str] = mapped_column(Text, default="")  # 200자 발췌
    outcome: Mapped[str] = mapped_column(String(20), default="unknown")
    # outcome: "success" / "uncertain" / "failed" / "unknown"
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


async def ensure_trajectory_table() -> None:
    """Create the trajectory table if missing (lifespan-safe)."""
    from sqlalchemy import text

    from app.core.database import engine

    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS farm_agent_trajectories ("
            "  id SERIAL PRIMARY KEY,"
            "  user_id VARCHAR(10) NOT NULL,"
            "  query TEXT NOT NULL,"
            "  route VARCHAR(40) NOT NULL,"
            "  tools_used TEXT NOT NULL DEFAULT '[]',"
            "  response_summary TEXT NOT NULL DEFAULT '',"
            "  outcome VARCHAR(20) NOT NULL DEFAULT 'unknown',"
            "  duration_ms INTEGER NOT NULL DEFAULT 0,"
            "  created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            ")"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_farm_trajectories_user_created "
            "ON farm_agent_trajectories(user_id, created_at DESC)"
        ))


# ── Recording ───────────────────────────────────────────────────────────────


async def record_trajectory(
    *,
    user_id: str,
    query: str,
    route: str,
    response_summary: str = "",
    tools_used: list[str] | None = None,
    outcome: str = "unknown",
    duration_ms: int = 0,
) -> None:
    """단일 에이전트 호출의 트라젝토리를 비동기 기록.

    실패가 호출자 흐름을 막지 않도록 모든 예외를 swallow + log. 본 함수는 fire-and-forget
    패턴으로 호출하면 된다 (asyncio.create_task).
    """
    try:
        async with async_session() as db:
            row = FarmAgentTrajectory(
                user_id=user_id[:10],  # 테이블 한도 방어
                query=query[:4000],    # 비대화적 4 KB 컷오프
                route=route[:40],
                tools_used=json.dumps(tools_used or [], ensure_ascii=False)[:2000],
                response_summary=response_summary[:600],
                outcome=outcome[:20],
                duration_ms=int(duration_ms),
            )
            db.add(row)
            await db.commit()
    except Exception:  # noqa: BLE001 — recording 실패가 사용자 흐름을 막지 않게
        logger.warning("trajectory.record_failed user=%s route=%s", user_id, route, exc_info=True)


# Strong references to in-flight trajectory tasks. Without this set, asyncio
# only holds a weak reference to tasks created via create_task; if the caller
# returns and nothing else awaits them, the GC may collect the task before its
# coroutine runs, dropping the trajectory silently and emitting
# "RuntimeWarning: coroutine ... was never awaited". Especially harmful in the
# /ask hot path where every request fires a record_async call.
_INFLIGHT_TRAJECTORY_TASKS: set[asyncio.Task] = set()


def record_async(**kwargs) -> None:
    """비동기 백그라운드 trajectory 기록 (호출자가 await 하지 않아도 됨).

    Task 는 _INFLIGHT_TRAJECTORY_TASKS 에 강한 참조를 남겨 GC 가 미실행 task 를
    수거하지 못하게 한다. 완료 시 done_callback 으로 set 에서 제거.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("trajectory.record_skipped no_running_loop route=%s", kwargs.get("route"))
        return
    task = loop.create_task(record_trajectory(**kwargs))
    _INFLIGHT_TRAJECTORY_TASKS.add(task)
    task.add_done_callback(_INFLIGHT_TRAJECTORY_TASKS.discard)


# ── Distillation ────────────────────────────────────────────────────────────


_STRATEGIES_PATH = Path(__file__).resolve().parents[3] / "memory" / "STRATEGIES.md"

_DISTILL_PROMPT = """\
당신은 농업 AI 비서 시스템의 학습 분석가입니다. 아래는 최근 {n} 건의 에이전트 실행
트라젝토리 요약입니다 (성공·실패 모두 포함).

## 트라젝토리
{trajectories}

## 당신의 작업
이 트라젝토리에서 **반복되는 패턴**과 **놓치고 있는 전략**을 찾아 ReasoningBank 형식의
새 전략을 1-3개 도출해주세요. 형식:

```
## R{next_id}. <한 줄 제목>
- **When**: <패턴이 적용되는 상황>
- **Strategy**: <취할 행동>
- **Pitfall**: <알려진 실패 모드 — 있을 때만>
- **Source**: distilled-{date} (n={n})
```

## 규칙
- 이미 STRATEGIES.md 에 있는 패턴과 중복 금지 (아래 기존 전략 참고).
- 매우 구체적이고 행동 가능해야 함 (vague advice 금지).
- 한 트라젝토리만 보고 일반화 금지 — 패턴이 ≥2 회 등장한 것만.
- 실패 트라젝토리에서 도출한 전략은 Pitfall 명시 필수.
- 새 전략을 못 찾으면 "NO_NEW_STRATEGIES" 한 줄만 출력.

## 기존 STRATEGIES.md (중복 회피용)
{existing_strategies}
"""


async def distill_strategies(
    *,
    days: int = 7,
    max_trajectories: int = 50,
) -> dict:
    """최근 N 일 트라젝토리를 읽어 새 전략을 LLM 으로 도출, STRATEGIES.md 에 append.

    반환: {"trajectories_read": N, "new_strategies": M, "appended": bool}.

    본 함수는 운영자가 수동으로 (e.g. POST /admin/farm-agent/distill) 또는 cron 으로
    호출. 매 사용자 요청에 동기적으로 트리거하지 말 것 — Sonnet 호출 1회 비용 발생.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stats = {"trajectories_read": 0, "new_strategies": 0, "appended": False}

    try:
        async with async_session() as db:
            rows_result = await db.execute(
                select(FarmAgentTrajectory)
                .where(FarmAgentTrajectory.created_at >= cutoff)
                .order_by(FarmAgentTrajectory.created_at.desc())
                .limit(max_trajectories)
            )
            rows = rows_result.scalars().all()
    except Exception:  # noqa: BLE001
        logger.exception("distill.read_trajectories_failed")
        return stats

    if not rows:
        logger.info("distill.no_trajectories days=%d", days)
        return stats

    stats["trajectories_read"] = len(rows)

    # Trajectory 요약 직렬화 — query 와 outcome 만 보면 패턴 도출 충분.
    summaries = []
    for r in rows:
        # tools_used 는 trajectory 기록 시 JSON 으로 직렬화돼 들어가지만, 손상된 row
        # (legacy migration / 부분 쓰기) 가 한 건만 있어도 distill 전체가 죽는다.
        # 빈 리스트로 폴백하고 row 단위 격리.
        if r.tools_used:
            try:
                tools = json.loads(r.tools_used)
            except (json.JSONDecodeError, TypeError):
                logger.warning("distill.tools_used_corrupt id=%s value=%r — skipped",
                               getattr(r, "id", "?"), str(r.tools_used)[:80])
                tools = []
        else:
            tools = []
        # Guard against legacy rows where tools_used was a JSON string scalar
        # rather than an array. Without this, tools[:5] would slice a string
        # (returning first 5 chars) and format it as if it were a tools list.
        if not isinstance(tools, list):
            logger.warning("distill.tools_used_not_list id=%s type=%s — coerced to []",
                           getattr(r, "id", "?"), type(tools).__name__)
            tools = []
        summaries.append(
            f"- [{r.outcome}] route={r.route} tools={tools[:5]} "
            f"q={r.query[:120]!r} resp={r.response_summary[:100]!r}"
        )
    trajectories_block = "\n".join(summaries)

    existing = ""
    if _STRATEGIES_PATH.is_file():
        existing = _STRATEGIES_PATH.read_text(encoding="utf-8")[:8000]  # 헤드 발췌

    # 다음 R{ID} 를 결정 (기존 R# 의 max+1)
    import re
    existing_ids = [int(m) for m in re.findall(r"^## R(\d+)\.", existing, re.MULTILINE)]
    next_id = (max(existing_ids) if existing_ids else 0) + 1

    prompt = _DISTILL_PROMPT.format(
        n=len(rows),
        trajectories=trajectories_block,
        next_id=next_id,
        date=datetime.now(timezone.utc).date().isoformat(),
        existing_strategies=existing,
    )

    try:
        from langchain_core.messages import HumanMessage

        from app.services.farm_agent.models import build_llm_for

        llm = build_llm_for("subsidy", max_tokens=2000)  # subsidy tier = sonnet, 분석 적합
        result = await llm.ainvoke([HumanMessage(content=prompt)])
        text_out = (result.content if hasattr(result, "content") else str(result)) or ""
    except Exception:  # noqa: BLE001
        logger.exception("distill.llm_failed")
        return stats

    if "NO_NEW_STRATEGIES" in text_out:
        logger.info("distill.no_new_strategies trajectories=%d", len(rows))
        return stats

    # 새 전략 카운트 (## R 헤더로)
    new_count = len(re.findall(r"^## R\d+\.", text_out, re.MULTILINE))
    if new_count == 0:
        logger.info("distill.no_valid_strategies_extracted")
        return stats

    # Append (안전: 기존 파일 보존, 끝에만 추가). 동기 파일 I/O 를 to_thread 로
    # 밀어 이벤트 루프 블로킹 방지 (NFS/느린 디스크 환경에서 다른 코루틴이 stall).
    block = (
        f"\n\n<!-- distilled {datetime.now(timezone.utc).isoformat()} "
        f"from {len(rows)} trajectories -->\n"
        f"{text_out.strip()}\n"
    )

    def _append_sync() -> None:
        with _STRATEGIES_PATH.open("a", encoding="utf-8") as f:
            f.write(block)

    try:
        await asyncio.to_thread(_append_sync)
        stats["new_strategies"] = new_count
        stats["appended"] = True
        logger.info("distill.appended new_strategies=%d trajectories=%d", new_count, len(rows))
    except Exception:  # noqa: BLE001
        logger.exception("distill.append_failed")

    return stats
