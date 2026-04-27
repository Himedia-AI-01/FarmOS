"""AI Agent decisions seed 스크립트.

Design Ref §8.6 — L2/L3 E2E 테스트용 30건 샘플.

실행:
    cd backend
    uv run python scripts/seed_ai_agent.py

멱등: `INSERT ... ON CONFLICT (id) DO NOTHING`. 재실행해도 중복 생성되지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# app 모듈 접근 위해 backend/ 를 sys.path 에 추가
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.core.database import async_session, init_db  # noqa: E402

CONTROL_TYPES = ["ventilation", "irrigation", "lighting", "shading"]
PRIORITIES = ["emergency", "high", "medium", "low"]
SOURCES = ["rule", "llm", "tool", "manual"]

REASONS = {
    "ventilation": [
        "CO2 450ppm, 내부 29도 → 창문 60% 개방",
        "외기 대비 내부 습도 85% → 팬 가속",
        "일몰 후 야간 환기 모드 전환",
    ],
    "irrigation": [
        "토양수분 52% — 임계값 이하 → 밸브 열림",
        "작물 흡수량 예측 기반 20분 관수",
        "N:P:K 비율 재조정 (1.2 : 1.0 : 0.8)",
    ],
    "lighting": [
        "조도 18000lux 부족 → 보조등 점등",
        "일출 이후 보조광 OFF",
        "개화 촉진 광주기 조정",
    ],
    "shading": [
        "오후 2시 강일사 → 차광막 40%",
        "야간 온도 저하 예보 → 보온커튼 70%",
        "흐림 전환 → 차광 해제",
    ],
}


def _make_decision(now: datetime, i: int) -> dict:
    ct = CONTROL_TYPES[i % 4]
    pr = PRIORITIES[i % 4]
    src = SOURCES[i % 4]
    ts = now - timedelta(minutes=i * 17)  # 과거로 분산
    reasons = REASONS[ct]
    return {
        "id": str(uuid.uuid4()),
        "timestamp": ts,
        "control_type": ct,
        "priority": pr,
        "source": src,
        "reason": reasons[i % len(reasons)],
        "action": {
            "ventilation": {"window_open_pct": 50 + (i % 5) * 10, "fan_speed": 800 + i * 20},
            "irrigation": {"valve_open": True, "duration_s": 60 + i * 10},
            "lighting": {"on": True, "brightness_pct": 60 + (i % 4) * 10},
            "shading": {"shade_pct": 30 + (i % 5) * 10, "insulation_pct": 0},
        }[ct],
        "tool_calls": [
            {
                "tool": {
                    "ventilation": "open_window",
                    "irrigation": "open_valve",
                    "lighting": "set_brightness",
                    "shading": "set_shade",
                }[ct],
                "arguments": {"pct": 50 + (i % 5) * 10},
                "result": {"success": (i % 7 != 0)},  # 가끔 실패 행 섞기
            }
        ],
        "sensor_snapshot": {
            "temperature": round(22.0 + (i % 10) * 0.8, 1),
            "humidity": 55 + (i % 15),
            "light_intensity": 15000 + i * 400,
            "soil_moisture": round(50.0 + (i % 8) * 1.5, 1),
            "timestamp": ts.isoformat(),
        },
        "duration_ms": 200 + (i * 17) % 600,
    }


async def seed(count: int = 30) -> tuple[int, int]:
    await init_db()  # 테이블이 없으면 생성 (멱등)
    now = datetime.now(timezone.utc)

    inserted_decisions = 0
    summary_bumps = 0

    async with async_session() as db:
        for i in range(count):
            d = _make_decision(now, i)

            # 1) 원본 insert (멱등)
            result = await db.execute(
                text(
                    """
                    INSERT INTO ai_agent_decisions
                        (id, timestamp, control_type, priority, source, reason,
                         action, tool_calls, sensor_snapshot, duration_ms, created_at)
                    VALUES
                        (:id, :ts, :ct, :pr, :src, :reason,
                         CAST(:action AS jsonb), CAST(:tool_calls AS jsonb),
                         CAST(:snapshot AS jsonb), :dur, now())
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {
                    "id": d["id"],
                    "ts": d["timestamp"],
                    "ct": d["control_type"],
                    "pr": d["priority"],
                    "src": d["source"],
                    "reason": d["reason"],
                    "action": json.dumps(d["action"]),
                    "tool_calls": json.dumps(d["tool_calls"]),
                    "snapshot": json.dumps(d["sensor_snapshot"]),
                    "dur": d["duration_ms"],
                },
            )
            if result.rowcount == 0:
                continue  # 이미 존재
            inserted_decisions += 1

            # 2) 일별 집계 UPSERT
            await db.execute(
                text(
                    """
                    INSERT INTO ai_agent_activity_daily
                        (day, control_type, count, by_source, by_priority,
                         avg_duration_ms, duration_count, duration_sum,
                         last_at, updated_at)
                    VALUES
                        (:day, :ct, 1,
                         jsonb_build_object(CAST(:src AS text), 1),
                         jsonb_build_object(CAST(:pr AS text), 1),
                         CAST(:dur AS INTEGER),
                         CASE WHEN :dur IS NULL THEN 0 ELSE 1 END,
                         CASE WHEN :dur IS NULL THEN 0 ELSE CAST(:dur AS INTEGER) END,
                         :last_at, now())
                    ON CONFLICT (day, control_type) DO UPDATE SET
                        count = ai_agent_activity_daily.count + 1,
                        by_source = jsonb_set(
                            ai_agent_activity_daily.by_source,
                            ARRAY[CAST(:src AS text)],
                            to_jsonb(COALESCE((ai_agent_activity_daily.by_source->>:src)::int, 0) + 1)
                        ),
                        by_priority = jsonb_set(
                            ai_agent_activity_daily.by_priority,
                            ARRAY[CAST(:pr AS text)],
                            to_jsonb(COALESCE((ai_agent_activity_daily.by_priority->>:pr)::int, 0) + 1)
                        ),
                        duration_count = ai_agent_activity_daily.duration_count
                            + CASE WHEN :dur IS NULL THEN 0 ELSE 1 END,
                        duration_sum = ai_agent_activity_daily.duration_sum
                            + CASE WHEN :dur IS NULL THEN 0 ELSE CAST(:dur AS INTEGER) END,
                        avg_duration_ms = CASE
                            WHEN (
                                ai_agent_activity_daily.duration_count
                                + CASE WHEN :dur IS NULL THEN 0 ELSE 1 END
                            ) = 0
                                THEN ai_agent_activity_daily.avg_duration_ms
                            ELSE ROUND(
                                (
                                    ai_agent_activity_daily.duration_sum
                                    + CASE WHEN :dur IS NULL THEN 0 ELSE CAST(:dur AS INTEGER) END
                                )::numeric
                                / NULLIF(
                                    ai_agent_activity_daily.duration_count
                                    + CASE WHEN :dur IS NULL THEN 0 ELSE 1 END,
                                    0
                                )
                            )::int
                        END,
                        last_at = GREATEST(ai_agent_activity_daily.last_at, :last_at),
                        updated_at = now()
                    """
                ),
                {
                    "day": d["timestamp"].date(),
                    "ct": d["control_type"],
                    "src": d["source"],
                    "pr": d["priority"],
                    "dur": d["duration_ms"],
                    "last_at": d["timestamp"],
                },
            )

            # 3) 시간별 집계 UPSERT
            hour_bucket = d["timestamp"].replace(minute=0, second=0, microsecond=0)
            await db.execute(
                text(
                    """
                    INSERT INTO ai_agent_activity_hourly
                        (hour, control_type, count, by_source, by_priority, last_at, updated_at)
                    VALUES
                        (:hour, :ct, 1,
                         jsonb_build_object(CAST(:src AS text), 1),
                         jsonb_build_object(CAST(:pr AS text), 1),
                         :last_at, now())
                    ON CONFLICT (hour, control_type) DO UPDATE SET
                        count = ai_agent_activity_hourly.count + 1,
                        by_source = jsonb_set(
                            ai_agent_activity_hourly.by_source,
                            ARRAY[CAST(:src AS text)],
                            to_jsonb(COALESCE((ai_agent_activity_hourly.by_source->>:src)::int, 0) + 1)
                        ),
                        by_priority = jsonb_set(
                            ai_agent_activity_hourly.by_priority,
                            ARRAY[CAST(:pr AS text)],
                            to_jsonb(COALESCE((ai_agent_activity_hourly.by_priority->>:pr)::int, 0) + 1)
                        ),
                        last_at = GREATEST(ai_agent_activity_hourly.last_at, :last_at),
                        updated_at = now()
                    """
                ),
                {
                    "hour": hour_bucket,
                    "ct": d["control_type"],
                    "src": d["source"],
                    "pr": d["priority"],
                    "last_at": d["timestamp"],
                },
            )
            summary_bumps += 1

        await db.commit()

    return inserted_decisions, summary_bumps


async def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    inserted, bumps = await seed(count)
    print(f"[seed_ai_agent] inserted_decisions={inserted} summary_bumps={bumps} (requested={count})")


if __name__ == "__main__":
    asyncio.run(main())
