"""헬스체크 — DB + 주요 서브시스템 상태 노출."""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """서버 상태.

    DB 연결 + 주요 서브시스템 ready 플래그를 한 번에 노출.
    운영자는 단일 GET 으로 어떤 컴포넌트가 degraded 인지 즉시 식별 가능.
    """
    try:
        await db.execute(select(1))
        db_ok = True
    except Exception as exc:
        logger.exception("health_check.db_failed err=%s", exc)
        db_ok = False

    state = request.app.state
    subsystems = {
        "subsidy_rag": bool(getattr(state, "subsidy_rag_ready", False)),
        "briefing": bool(getattr(state, "briefing_ready", False)),
        "farm_agent": bool(getattr(state, "farm_agent_ready", False)),
        "ai_agent_bridge": bool(
            getattr(state, "ai_agent_bridge", None) is not None
            and getattr(getattr(state, "ai_agent_bridge", None), "healthy", False)
        ),
    }

    cleanup_failures = int(getattr(state, "cleanup_consecutive_failures", 0))

    overall = "ok" if db_ok and all(subsystems.values()) else "degraded"

    return {
        "status": overall,
        "storage": "postgres",
        "db_ok": db_ok,
        "subsystems": subsystems,
        "cleanup_consecutive_failures": cleanup_failures,
    }
