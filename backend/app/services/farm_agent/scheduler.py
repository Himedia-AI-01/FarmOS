"""07:00 KST 자동 브리핑 사전 생성 스케줄러.

설계:
  - 외부 의존(APScheduler 등) 없이 asyncio.create_task 로 단일 long-running 루프.
  - 매 사이클: 다음 07:00 KST 까지 sleep → 모든 활성 사용자 브리핑 사전 생성 → 반복.
  - 서버 재시작이 07:00 이후라면 그 날은 사전 생성을 건너뛰고 (캐시 미스 → 사용자
    호출 시 on-demand 생성 폴백) 다음 날 07:00 을 기다린다.
  - 단일 실패가 루프를 죽이지 않도록 사이클 단위 try/except.

KST 고정: zoneinfo.Asia/Seoul 사용 (서버 OS 타임존 무관).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.farm_agent.briefing import pregenerate_all_users

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
_TRIGGER_HOUR = 7  # 07:00 KST


def _seconds_until_next_trigger(now: datetime | None = None) -> float:
    """현재 시각(KST) 기준 다음 07:00 KST 까지의 초 수."""
    now = now or datetime.now(KST)
    target = now.replace(hour=_TRIGGER_HOUR, minute=0, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return (target - now).total_seconds()


# Cap each individual sleep window so the scheduler tolerates wall-clock
# jumps from VM suspend/resume, NTP corrections, or container migration.
# Without this cap, a 22-hour sleep would simply fire late if the VM was
# suspended mid-sleep — recompute hourly to catch up.
_MAX_SLEEP_CHUNK_SEC = 60 * 60  # 1h


async def run_briefing_scheduler(app) -> None:
    """무한 루프: 다음 07:00 KST 까지 sleep → 전체 사용자 브리핑 사전 생성 → 반복.

    app.state.farm_agent 가 None 이면 (초기화 실패) 한 사이클 건너뛴다.

    Wall-clock robustness: 매번 _MAX_SLEEP_CHUNK_SEC 를 sleep 한 뒤 다음 trigger 까지
    남은 시간을 재계산. VM suspend/resume·NTP 보정 등 시간 점프에 강건함.
    """
    logger.info("briefing.scheduler.started timezone=Asia/Seoul trigger_hour=%d", _TRIGGER_HOUR)
    while True:
        try:
            # Sleep in chunks until we reach the trigger time, recomputing each
            # chunk from the current wall-clock so we adapt to clock jumps.
            while True:
                remaining = _seconds_until_next_trigger()
                if remaining <= 0:
                    break
                chunk = min(remaining, _MAX_SLEEP_CHUNK_SEC)
                logger.debug("briefing.scheduler.sleeping_chunk seconds=%.0f remaining=%.0f",
                             chunk, remaining)
                await asyncio.sleep(chunk)

            agent = getattr(app.state, "farm_agent", None)
            if agent is None:
                logger.warning("briefing.scheduler.skip_cycle agent_not_ready")
                # agent 초기화 대기 — 1분 후 재시도하지 않고 다음 07:00 으로.
                # 즉시 continue 하면 _seconds_until_next_trigger() 가 다음 날 07:00 까지
                # 다시 sleep 하므로 CPU spin 위험 없음.
                continue

            stats = await pregenerate_all_users(agent)
            logger.info("briefing.scheduler.cycle_done %s", stats)
        except asyncio.CancelledError:
            logger.info("briefing.scheduler.cancelled")
            raise
        except Exception:  # noqa: BLE001 — 단일 실패가 루프를 죽이지 않게
            logger.exception("briefing.scheduler.cycle_failed")
            # If `_seconds_until_next_trigger` itself ever raises (e.g. zoneinfo
            # missing), the bare `continue` would loop instantly with no sleep
            # and CPU-spin until something else breaks. Floor the recovery
            # interval at 60s so we always yield.
            await asyncio.sleep(60)
