"""Trajectory replay — re-runs recent production queries through the current
agent and compares outputs. Regression detection on real traffic.

Reads from `farm_agent_trajectories` (populated by ReasoningBank). For each
recent query, replays it; if the new outcome is `failed` while the historical
was `success`, that's a regression and the test fails.

Strict semantic comparison isn't feasible (LLM paraphrase). We compare outcome
labels only. Final-answer drift can be inspected manually via the report.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.eval, pytest.mark.integration]


async def _load_recent(days: int = 3, limit: int = 20):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.core.database import async_session
    from app.services.farm_agent.reasoning_bank import FarmAgentTrajectory

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with async_session() as db:
        result = await db.execute(
            select(FarmAgentTrajectory)
            .where(FarmAgentTrajectory.created_at >= cutoff)
            .where(FarmAgentTrajectory.outcome.in_(["success", "uncertain"]))
            .order_by(FarmAgentTrajectory.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()


async def _replay(query: str, user_id: str) -> str:
    """Replay a query through the underlying LLM (orchestrator route).

    Skips the full agent for cost — runs only the orchestrator-level synthesis
    using farm_data tools to detect "did the same query still produce *something*".
    Full re-run via the production agent would be cleaner but expensive.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from langgraph.prebuilt import create_react_agent

    from app.services.farm_agent.models import build_llm_for
    from app.services.farm_agent.prompts import FARM_DATA_PROMPT
    from app.services.farm_agent.tools import FARM_DATA_TOOLS

    llm = build_llm_for("farm_data", max_tokens=512)
    agent = create_react_agent(
        llm,
        tools=list(FARM_DATA_TOOLS),
        prompt=SystemMessage(content=FARM_DATA_PROMPT),
    )
    state = await agent.ainvoke(
        {"messages": [HumanMessage(content=query)]},
        config={"configurable": {"user_id": user_id}, "recursion_limit": 10},
    )
    msgs = state.get("messages", [])
    return getattr(msgs[-1], "content", "") if msgs else ""


async def test_trajectory_replay_smoke(record_result):
    rows = await _load_recent()
    if not rows:
        pytest.skip("No recent trajectories — use the chat first to populate.")

    regressions = []
    successes = 0
    for r in rows[:10]:  # cap at 10 to keep cost bounded
        answer = await _replay(r.query, r.user_id)
        # Heuristic: empty / very short answer = regression vs prior success.
        new_outcome = "success" if (answer and len(answer) > 30) else "failed"
        passed = new_outcome != "failed" or r.outcome == "failed"
        if not passed:
            regressions.append(
                f"q={r.query[:60]!r} historical={r.outcome} replay={new_outcome}"
            )
        else:
            successes += 1
        record_result(
            surface="trajectory_replay",
            case_id=r.query[:40],
            passed=passed,
            detail=f"historical={r.outcome} replay={new_outcome}",
            query=r.query,
            answer=answer,
        )

    if regressions:
        pytest.fail(
            f"{len(regressions)}/{len(rows[:10])} regressions:\n"
            + "\n".join(regressions)
        )
